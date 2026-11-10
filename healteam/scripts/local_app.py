"""Foreground local application supervisor; no provider requests or PID kills."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from local_runtime import LocalRuntimeError, WindowsChild, load_manifest, node_environment

FORMAT = "healteam-local-app-v1"


def _soft_model_issue(code: str) -> bool:
    return code.startswith("live_requires_") or code == "invalid_llm_base_url"


def _display(result: dict, *, human: bool) -> None:
    if not human:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    status = result.get("status")
    labels = {
        "ready": "运行环境检查通过",
        "needs_attention": "运行环境需要处理",
        "running": "HealTeam 正在运行",
        "starting": "HealTeam 正在启动",
        "stopping": "正在停止 HealTeam",
        "stopped": "HealTeam 已停止",
        "error": "本次操作未完成",
    }
    print(labels.get(status, "运行状态已读取"))
    if result.get("version"):
        print("项目版本：" + str(result["version"]))
    if status == "running" and type(result.get("web_port")) is int:
        print(f"网页地址：http://127.0.0.1:{result['web_port']}")
    if result.get("model_mode"):
        print("模型模式：" + ("本地离线" if result["model_mode"] == "mock" else "真实模型"))
    for code in result.get("issues", [result["code"]] if result.get("code") else []):
        suggestions = {
            "runtime_not_prepared": "缺少或损坏预构建文件，请先运行首次准备入口。",
            "runtime_hash_mismatch": "预构建文件校验未通过，请停止服务后重新准备。",
            "runtime_inventory_mismatch": "预构建目录出现额外或缺少文件，请停止服务后重新准备。",
            "runtime_version_requires_rebuild": "前后端版本不匹配，请停止服务后重新准备。",
            "runtime_cache_requires_rebuild": "预构建配置需要更新，请停止服务后重新准备。",
            "node_unavailable": "未找到 Node.js，请安装项目要求的 Node.js 后重新准备。",
            "node_build_version_mismatch": "Node.js 与预构建版本不同，请停止服务后重新准备。",
            "python_dependencies_missing": "Python 运行依赖不完整，请运行首次准备入口。",
            "local_port_in_use": "端口被其他程序占用；请检查运行状态，不会停止其他程序。",
            "local_port_unavailable": "系统暂不允许使用该本机端口，请按可用端口重新准备运行文件。",
            "api_port_build_mismatch": "API 端口与预构建配置不符，请按目标端口重新准备。",
            "live_requires_api_key": "真实模型密钥尚未填写，可先补齐本机配置或使用离线模式。",
            "live_requires_model": "真实模型名称尚未填写，请补齐本机配置。",
        }
        print("- " + suggestions.get(code, "请按运行说明处理：" + str(code)))


def workspace_id(root: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()


def _bootstrap(root: Path, *, include_agents: bool = True) -> None:
    sys.path[:0] = [str(root), str(root / "apps" / "api")]
    from repo_bootstrap import register_package

    register_package("healteam_memory", root / "packages" / "memory")
    if include_agents:
        register_package("healteam_agents", root / "packages" / "agents")


def _request(
    url: str, *, method: str = "GET", headers: dict | None = None, timeout: float = 2
) -> tuple[int, dict]:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, _request, _file, _code, _message, _headers, _new_url):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(65537)
        if len(body) > 65536:
            raise LocalRuntimeError("local_response_invalid")
        return response.status, json.loads(body.decode("utf-8"))


def _regular_state_file(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _state_path(state_dir: Path) -> Path:
    # Resolve the stable directory once. Resolving an atomically replaced leaf
    # on Windows can transiently retain a \\?\ prefix and fail containment.
    path = state_dir.resolve() / "state.json"
    try:
        if not _regular_state_file(path.lstat()):
            raise LocalRuntimeError("local_state_invalid")
    except FileNotFoundError:
        pass
    return path


def _read_state(root: Path, state_dir: Path) -> dict | None:
    try:
        path = _state_path(state_dir)
        try:
            with path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                if not _regular_state_file(info) or info.st_size > 16384:
                    raise ValueError
                body = stream.read(16385)
        except FileNotFoundError:
            # State is published atomically and removed during normal shutdown.
            return None
        if len(body) > 16384:
            raise ValueError
        data = json.loads(body.decode("utf-8"))
        if (
            data.get("format") != FORMAT
            or data.get("workspace") != workspace_id(root)
            or not re.fullmatch(r"[0-9a-f]{32}", data.get("instance_id", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", data.get("token", ""))
            or type(data.get("control_port")) is not int
            or not 1 <= data["control_port"] <= 65535
        ):
            raise ValueError
        return data
    except LocalRuntimeError:
        raise
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError):
        raise LocalRuntimeError("local_state_invalid") from None


def control(root: Path, state_dir: Path, action: str = "status") -> dict:
    state = _read_state(root, state_dir)
    if state is None:
        return {"status": "stopped"}
    endpoint = f"http://127.0.0.1:{state['control_port']}/_healteam/{state['instance_id']}"
    headers = {"X-HealTeam-Control": state["token"]}
    try:
        code, body = _request(endpoint + "/status", headers=headers)
        if code != 200 or any(body.get(key) != state[key] for key in ("format", "instance_id", "workspace")):
            raise LocalRuntimeError("local_state_unverified")
        if action == "stop":
            code, body = _request(endpoint + "/stop", method="POST", headers=headers)
            if code != 200:
                raise LocalRuntimeError("local_stop_failed")
        # Never return the token, process command line, configuration or URL.
        return {key: body[key] for key in ("status", "pid", "api_port", "web_port", "version") if key in body}
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return {"status": "stopped", "reason": "previous_instance_not_running"}
    except (ValueError, TypeError, KeyError):
        raise LocalRuntimeError("local_state_unverified") from None


def _node_version(node: str) -> int:
    try:
        result = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            env=node_environment(0),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        match = re.fullmatch(r"v(\d+)\.\d+\.\d+\s*", result.stdout)
        if result.returncode or match is None:
            raise ValueError
        return int(match[1])
    except (OSError, ValueError, subprocess.SubprocessError):
        raise LocalRuntimeError("node_unavailable") from None


def doctor(root: Path, *, api_port: int | None = None) -> dict:
    issues = []
    checks: dict[str, str] = {}
    public: dict[str, Any] = {}
    if not (3, 11) <= sys.version_info[:2] < (3, 13):
        issues.append("python_version_unsupported")
    checks["python"] = "available" if not issues else "invalid"
    if os.name != "nt" or sys.maxsize <= 2**32:
        issues.append("windows_x64_launcher_required")
    for dependency in ("uvicorn", "fastapi", "dotenv", "sqlalchemy", "aiosqlite"):
        if importlib.util.find_spec(dependency) is None:
            issues.append("python_dependencies_missing")
            break
    checks["python_dependencies"] = "invalid" if "python_dependencies_missing" in issues else "available"
    manifest = None
    try:
        manifest = load_manifest(root, api_port=api_port)
        checks["runtime"] = "verified"
    except LocalRuntimeError as exc:
        issues.append(exc.code)
        checks["runtime"] = "invalid"
    node = shutil.which("node")
    try:
        if node is None:
            raise LocalRuntimeError("node_unavailable")
        major = _node_version(node)
        if not 22 <= major < 25:
            raise LocalRuntimeError("node_version_unsupported")
        if manifest is not None and major != manifest["node_major"]:
            raise LocalRuntimeError("node_build_version_mismatch")
        checks["node"] = "available"
    except LocalRuntimeError as exc:
        issues.append(exc.code)
        checks["node"] = "invalid"
    try:
        _bootstrap(root)
        from app.settings import ConfigurationError, load_settings
        from dotenv import load_dotenv

        load_dotenv(root / ".env", override=False)
        settings = load_settings(require_live_credentials=False)
        public = settings.public_status()
        try:
            settings.check_live_configuration()
            checks["model_configuration"] = "configured"
        except ConfigurationError as exc:
            issues.append(str(exc))
            checks["model_configuration"] = "invalid"
    except Exception:
        issues.append("configuration_unavailable")
        checks["model_configuration"] = "invalid"
    try:
        version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    except (OSError, ValueError, KeyError):
        version = "unknown"
    return {
        "status": "ready" if not issues else "needs_attention",
        "version": version,
        **public,
        "checks": checks,
        "issues": list(dict.fromkeys(issues)),
    }


def _reserve_socket(port: int) -> socket.socket:
    sock = socket.socket()
    try:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
        sock.setblocking(False)
        return sock
    except OSError:
        sock.close()
        with socket.socket() as probe:
            probe.settimeout(0.15)
            occupied = probe.connect_ex(("127.0.0.1", port)) == 0
        raise LocalRuntimeError("local_port_in_use" if occupied else "local_port_unavailable") from None


class _ControlServer(ThreadingHTTPServer):
    daemon_threads = True


def start(
    root: Path,
    state_dir: Path,
    *,
    api_port: int | None,
    web_port: int,
    no_browser: bool,
    startup_timeout: float,
) -> int:
    _bootstrap(root, include_agents=False)
    from healteam_memory.runtime_lease import RuntimeInUseError, RuntimeLease

    state_dir.mkdir(parents=True, exist_ok=True)
    lock = RuntimeLease(state_dir / "supervisor")
    try:
        lock.acquire()
    except RuntimeInUseError:
        existing = control(root, state_dir)
        if existing["status"] == "running":
            print(json.dumps({**existing, "status": "already_running"}, ensure_ascii=False))
            if not no_browser:
                webbrowser.open(f"http://127.0.0.1:{existing['web_port']}")
            return 0
        raise LocalRuntimeError("local_instance_busy") from None
    runtime_lock = RuntimeLease(root / ".local" / "runtime")
    api_socket = None
    control_server = None
    child = None
    api_server = None
    api_thread = None
    state: dict[str, Any] = {}
    stop_event = threading.Event()
    failure = []
    interrupted = False
    previous_signals = {}
    if threading.current_thread() is threading.main_thread():
        if os.name == "nt":
            import ctypes

            # The Windows venv redirector can inherit an ignored Ctrl+C flag.
            # Reset it in this owned foreground process, never in its parent.
            ctypes.WinDLL("kernel32").SetConsoleCtrlHandler(None, False)

        def request_stop(_number, _frame):
            nonlocal interrupted
            # Signal handlers must not acquire Event/Condition locks.
            interrupted = True

        for number in (signal.SIGINT, *([signal.SIGBREAK] if os.name == "nt" else [])):
            previous_signals[number] = signal.signal(number, request_stop)
    try:
        runtime_lock.acquire()
        manifest = load_manifest(root, api_port=api_port)
        api_port = manifest["api_port"]
        if api_port == web_port:
            raise LocalRuntimeError("api_web_port_collision")
        api_socket = _reserve_socket(api_port)
        probe = _reserve_socket(web_port)
        probe.close()
        # Fail port conflicts before importing slower model/provider modules.
        result = doctor(root, api_port=api_port)
        hard_issues = [issue for issue in result["issues"] if not _soft_model_issue(issue)]
        if hard_issues:
            raise LocalRuntimeError(hard_issues[0])
        if stop_event.is_set() or interrupted:
            return 0
        instance_id = uuid.uuid4().hex
        token = os.urandom(32).hex()
        prefix = f"/_healteam/{instance_id}"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def respond(self, *, stop=False):
                if not hmac.compare_digest(self.headers.get("X-HealTeam-Control", ""), token):
                    self.send_error(403)
                    return
                expected = prefix + ("/stop" if stop else "/status")
                if self.path != expected:
                    self.send_error(404)
                    return
                if stop:
                    stop_event.set()
                    state["status"] = "stopping"
                body = json.dumps({key: value for key, value in state.items() if key != "token"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond(stop=True)

        control_server = _ControlServer(("127.0.0.1", 0), Handler)
        state.update(
            format=FORMAT,
            workspace=workspace_id(root),
            instance_id=instance_id,
            token=token,
            control_port=control_server.server_port,
            pid=os.getpid(),
            api_port=api_port,
            web_port=web_port,
            version=result["version"],
            status="starting",
            started_at=datetime.now(UTC).isoformat(),
        )
        threading.Thread(target=control_server.serve_forever, daemon=True).start()
        path = _state_path(state_dir)
        temporary = state_dir / f"state-{instance_id}.tmp"
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(path)
        import uvicorn
        from app.main import create_app

        api_server = uvicorn.Server(
            uvicorn.Config(
                create_app(),
                host="127.0.0.1",
                port=api_port,
                log_level="warning",
                timeout_graceful_shutdown=5,
            )
        )

        def run_api():
            try:
                api_server.run(sockets=[api_socket])
            except BaseException:
                failure.append("api_start_failed")

        api_thread = threading.Thread(target=run_api, daemon=True)
        api_thread.start()
        deadline = time.monotonic() + startup_timeout
        while not api_server.started:
            if stop_event.is_set() or interrupted:
                return 0
            if failure or not api_thread.is_alive() or time.monotonic() >= deadline:
                raise LocalRuntimeError("api_start_failed")
            time.sleep(0.05)
        ready_code, ready = _request(f"http://127.0.0.1:{api_port}/readyz")
        if ready_code != 200 and any(not _soft_model_issue(issue) for issue in ready.get("issues", [])):
            raise LocalRuntimeError("api_not_ready")
        node = shutil.which("node")
        assert node is not None
        child = WindowsChild(
            [node, str(root / ".local" / "runtime" / "web" / "server.js")],
            cwd=root / ".local" / "runtime" / "web",
            environment=node_environment(web_port),
            log_path=state_dir / "web.log",
        )
        while True:
            if stop_event.is_set() or interrupted:
                return 0
            if child.poll() is not None:
                raise LocalRuntimeError("web_start_failed")
            try:
                code, _ = _request(f"http://127.0.0.1:{web_port}/healthz")
                if code == 200:
                    break
            except (urllib.error.URLError, OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise LocalRuntimeError("web_start_timeout")
            time.sleep(0.1)
        state["status"] = "running"
        path = _state_path(state_dir)
        temporary = state_dir / f"state-{instance_id}.tmp"
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(path)
        print(f"HealTeam 已启动：http://127.0.0.1:{web_port}", flush=True)
        print("关闭此窗口或按 Ctrl+C 停止；也可运行停止服务入口。", flush=True)
        if ready_code != 200:
            print("模型配置尚待补齐；已保存的历史记录仍可查看。", flush=True)
        if not no_browser:
            webbrowser.open(f"http://127.0.0.1:{web_port}")
        while not stop_event.wait(0.2) and not interrupted:
            if child.poll() is not None:
                raise LocalRuntimeError("web_process_exited")
            if not api_thread.is_alive():
                raise LocalRuntimeError("api_process_exited")
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        if child is not None:
            child.close()
        if api_server is not None:
            api_server.should_exit = True
        if api_thread is not None:
            api_thread.join(timeout=12)
            if api_thread.is_alive() and api_server is not None:
                api_server.force_exit = True
                api_thread.join(timeout=2)
        if api_socket is not None:
            api_socket.close()
        if control_server is not None:
            control_server.shutdown()
            control_server.server_close()
        path = _state_path(state_dir)
        if path.exists():
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("instance_id") == state.get(
                    "instance_id"
                ):
                    path.unlink()
            except (OSError, ValueError):
                pass
        runtime_lock.release()
        lock.release()
        for number, handler in previous_signals.items():
            signal.signal(number, handler)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="启动、停止或检查本机 HealTeam；不发送模型请求")
    parser.add_argument("action", choices=["start", "status", "stop", "doctor"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--api-port", type=int)
    parser.add_argument("--web-port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--startup-timeout", type=float, default=45)
    parser.add_argument("--human", action="store_true", help="显示中文摘要，默认输出 JSON")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    state_dir = (args.state_dir or root / ".cache" / "local-app").expanduser().resolve()
    if (
        any(port is not None and not 1 <= port <= 65535 for port in (args.api_port, args.web_port))
        or not 1 <= args.startup_timeout <= 120
    ):
        parser.error("端口须在 1..65535，启动等待须在 1..120 秒")
    try:
        os.chdir(root)
        os.environ["HEALTEAM_ROOT"] = str(root)
        if args.action == "doctor":
            result = doctor(root, api_port=args.api_port)
            _display(result, human=args.human)
            return 0 if result["status"] == "ready" else 1
        if args.action == "status":
            _display(control(root, state_dir), human=args.human)
            return 0
        if args.action == "stop":
            result = control(root, state_dir, "stop")
            deadline = time.monotonic() + 20
            while result["status"] != "stopped" and time.monotonic() < deadline:
                time.sleep(0.1)
                result = control(root, state_dir)
            _display(result, human=args.human)
            return 0 if result["status"] == "stopped" else 1
        web_port = args.web_port if args.web_port is not None else int(os.getenv("WEB_PORT", "3000"))
        if not 1 <= web_port <= 65535:
            raise LocalRuntimeError("invalid_web_port")
        return start(
            root,
            state_dir,
            api_port=args.api_port,
            web_port=web_port,
            no_browser=args.no_browser,
            startup_timeout=args.startup_timeout,
        )
    except LocalRuntimeError as exc:
        _display(
            {
                "status": "error",
                "code": exc.code,
                "next_step": "请先运行运行检查；缺少预构建文件时运行 scripts/prepare_local.py。",
            },
            human=args.human,
        )
        return 1
    except Exception:
        _display({"status": "error", "code": "local_operation_failed"}, human=args.human)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
