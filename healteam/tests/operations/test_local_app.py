"""Local supervisor safety and real Windows foreground lifecycle acceptance."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from local_app import FORMAT, control, doctor, workspace_id  # noqa: E402
from local_runtime import LocalRuntimeError, WindowsChild, load_manifest, node_environment  # noqa: E402


def manifest_root(tmp_path):
    root = tmp_path / "project"
    runtime = root / ".local/runtime"
    files = {
        "web/server.js": b"server",
        "web/.next/static/test.js": b"static",
        "web/.next/required-server-files.json": b'{"config":{"experimental":{"isrFlushToDisk":false}}}',
    }
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nversion="0.6.0"\n', encoding="utf-8")
    for name, content in files.items():
        path = runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    manifest = {
        "format": "healteam-local-runtime-v1",
        "entry": "web/server.js",
        "version": "0.6.0",
        "platform": "win32",
        "arch": "x64",
        "node_major": 24,
        "api_internal_url": "http://127.0.0.1:8000",
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    (runtime / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, manifest


def test_manifest_hashes_and_exact_loopback_api_port(tmp_path):
    root, _ = manifest_root(tmp_path)
    assert load_manifest(root)["api_port"] == 8000
    with pytest.raises(LocalRuntimeError, match="api_port_build_mismatch"):
        load_manifest(root, api_port=8001)
    (root / ".local/runtime/web/server.js").write_text("changed", encoding="utf-8")
    with pytest.raises(LocalRuntimeError, match="runtime_hash_mismatch"):
        load_manifest(root)


@pytest.mark.parametrize("name", ["web/.env", "web/extra.js"])
def test_runtime_inventory_rejects_unlisted_environment_or_executable_files(tmp_path, name):
    root, _ = manifest_root(tmp_path)
    (root / ".local/runtime" / name).write_text("unlisted", encoding="utf-8")
    with pytest.raises(LocalRuntimeError, match="runtime_inventory_mismatch"):
        load_manifest(root)


def test_runtime_version_must_match_the_source_backend(tmp_path):
    root, _ = manifest_root(tmp_path)
    (root / "pyproject.toml").write_text('[project]\nversion="0.7.0"\n', encoding="utf-8")
    with pytest.raises(LocalRuntimeError, match="runtime_version_requires_rebuild"):
        load_manifest(root)


def test_same_version_runtime_with_disk_route_cache_requires_rebuild(tmp_path):
    root, manifest = manifest_root(tmp_path)
    name = "web/.next/required-server-files.json"
    content = b'{"config":{"experimental":{"isrFlushToDisk":true}}}'
    (root / ".local/runtime" / name).write_bytes(content)
    manifest["files"][name] = hashlib.sha256(content).hexdigest()
    (root / ".local/runtime/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(LocalRuntimeError, match="runtime_cache_requires_rebuild"):
        load_manifest(root)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com",
        "http://localhost:8000",
        "http://127.0.0.1:8000/path",
        "http://key@127.0.0.1:8000",
        "http://127.0.0.1:8000?key=secret",
    ],
)
def test_manifest_cannot_redirect_api_or_credentials_outside_loopback(tmp_path, endpoint):
    root, manifest = manifest_root(tmp_path)
    manifest["api_internal_url"] = endpoint
    (root / ".local/runtime/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(LocalRuntimeError):
        load_manifest(root)


@pytest.mark.parametrize(
    "relative",
    ["", ".", "../private.env", "web/../../private.env", "C:/outside", "web\\outside", "/absolute"],
)
def test_manifest_paths_cannot_escape_runtime(tmp_path, relative):
    root, manifest = manifest_root(tmp_path)
    manifest["files"][relative] = "0" * 64
    (root / ".local/runtime/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(LocalRuntimeError):
        load_manifest(root)


def test_runtime_root_link_is_rejected_before_reading_external_manifest(tmp_path):
    root = tmp_path / "project"
    (root / ".local").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / ".local/runtime").symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi

        try:
            _winapi.CreateJunction(str(outside), str(root / ".local/runtime"))
        except OSError:
            pytest.skip("directory symlink/junction creation unavailable")
    with pytest.raises(LocalRuntimeError):
        load_manifest(root)


def test_node_environment_never_inherits_provider_or_control_secrets(monkeypatch):
    for name in (
        "HEALTEAM_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "NODE_OPTIONS",
        "HTTP_PROXY",
    ):
        monkeypatch.setenv(name, "do-not-forward")
    environment = node_environment(3001)
    assert "do-not-forward" not in json.dumps(environment)
    assert environment["HOSTNAME"] == "127.0.0.1" and environment["PORT"] == "3001"


def test_forged_state_never_sends_stop_or_kills_any_pid(tmp_path):
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = json.dumps({"format": "another-service"}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posts.append(self.path)
            self.send_response(200)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state = {
        "format": FORMAT,
        "workspace": workspace_id(tmp_path),
        "instance_id": "a" * 32,
        "token": "b" * 64,
        "control_port": server.server_port,
        "pid": 1,
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    try:
        with pytest.raises(LocalRuntimeError, match="local_state_unverified"):
            control(tmp_path, tmp_path, "stop")
        assert posts == []
    finally:
        server.shutdown()
        server.server_close()


def synthetic_state(root, state_dir):
    state = {
        "format": FORMAT,
        "workspace": workspace_id(root),
        "instance_id": "a" * 32,
        "token": "b" * 64,
        "control_port": 18001,
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path, state


def test_state_read_does_not_compare_a_racing_windows_leaf_resolve(tmp_path, monkeypatch):
    import local_app

    state_dir = tmp_path / "control"
    path, state = synthetic_state(tmp_path, state_dir)
    original = Path.resolve

    def racing_resolve(candidate, *arguments, **keywords):
        resolved = original(candidate, *arguments, **keywords)
        if candidate == path:
            # Windows realpath can retain this prefix if its second handle lookup
            # races with the supervisor's atomic state publication.
            return Path("\\\\?\\" + str(resolved))
        return resolved

    monkeypatch.setattr(Path, "resolve", racing_resolve)
    assert local_app._read_state(tmp_path, state_dir) == state


def test_state_disappearing_before_open_is_a_stopped_instance(tmp_path, monkeypatch):
    import local_app

    state_dir = tmp_path / "control"
    path, _state = synthetic_state(tmp_path, state_dir)
    original = Path.open

    def disappearing_open(candidate, *arguments, **keywords):
        if candidate == path:
            candidate.unlink(missing_ok=True)
        return original(candidate, *arguments, **keywords)

    monkeypatch.setattr(Path, "open", disappearing_open)
    assert local_app._read_state(tmp_path, state_dir) is None


def test_state_reparse_point_is_rejected_without_following_it(tmp_path, monkeypatch):
    import local_app

    state_dir = tmp_path / "control"
    path, _state = synthetic_state(tmp_path, state_dir)
    original = Path.lstat

    def reparse_metadata(candidate, *arguments, **keywords):
        if candidate == path:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_file_attributes=0x400)
        return original(candidate, *arguments, **keywords)

    monkeypatch.setattr(Path, "lstat", reparse_metadata)
    with pytest.raises(LocalRuntimeError, match="local_state_invalid"):
        local_app._read_state(tmp_path, state_dir)


def test_doctor_is_read_only_and_does_not_emit_secrets(tmp_path, monkeypatch):
    import local_app
    from app.settings import Settings

    root, _ = manifest_root(tmp_path)
    (root / "pyproject.toml").write_text('[project]\nversion="0.6.0"\n', encoding="utf-8")
    monkeypatch.setattr(local_app, "_bootstrap", lambda _root: None)
    monkeypatch.setattr(local_app, "_node_version", lambda _node: 24)
    monkeypatch.setattr(
        "app.settings.load_settings", lambda **_kwargs: Settings(model_mode="mock", api_key="do-not-disclose")
    )
    result = doctor(root)
    assert result["version"] == "0.6.0"
    assert "do-not-disclose" not in json.dumps(result)
    assert not (root / "data").exists() and not (root / ".cache").exists()


def test_local_control_never_follows_an_unverified_redirect(tmp_path):
    forwarded = []

    class Target(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            forwarded.append(self.path)
            self.send_response(200)
            self.end_headers()

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)

    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/unexpected")
            self.end_headers()
            self.wfile.write(b"{}")

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    for server in (target, redirect):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    state = {
        "format": FORMAT,
        "workspace": workspace_id(tmp_path),
        "instance_id": "a" * 32,
        "token": "b" * 64,
        "control_port": redirect.server_port,
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    try:
        with pytest.raises(LocalRuntimeError, match="local_state_unverified"):
            control(tmp_path, tmp_path, "stop")
        assert forwarded == []
    finally:
        for server in (redirect, target):
            server.shutdown()
            server.server_close()


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def port_open(port):
    with socket.socket() as probe:
        probe.settimeout(0.15)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def wait_closed(*ports):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if not any(port_open(port) for port in ports):
            return
        time.sleep(0.05)
    raise AssertionError("Owned listener did not close")


@pytest.mark.skipif(os.name != "nt", reason="Atomic Job-list creation is Windows-specific")
def test_owned_node_job_terminates_on_handle_close(tmp_path):
    node = shutil.which("node")
    assert node
    port = free_port()
    script = f"require('http').createServer((q,s)=>s.end('ok')).listen({port},'127.0.0.1')"
    child = WindowsChild(
        [node, "-e", script], cwd=tmp_path, environment=node_environment(port), log_path=tmp_path / "node.log"
    )
    try:
        deadline = time.monotonic() + 5
        while not port_open(port) and time.monotonic() < deadline:
            assert child.poll() is None
            time.sleep(0.05)
        assert port_open(port)
    finally:
        child.close()
    wait_closed(port)


@pytest.fixture
def prepared_runtime():
    if os.name != "nt":
        pytest.skip("Local launcher targets Windows x64")
    if not (ROOT / ".local/runtime/manifest.json").is_file():
        pytest.skip("Prepare the local runtime for production lifecycle acceptance")
    manifest = load_manifest(ROOT)
    if port_open(manifest["api_port"]):
        pytest.skip("The prepared API port is in use; never stop another service")
    return manifest


def launcher_environment(tmp_path):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HEALTEAM_", "ANTHROPIC_", "OPENAI_", "DEEPSEEK_", "LLM_"))
    }
    environment.update(
        HEALTEAM_MODEL_MODE="mock",
        HEALTEAM_DATABASE_URL=str(tmp_path / "training.sqlite3"),
        PYTHONUTF8="1",
        HEALTEAM_LLM_PROVIDER="openai",
        HEALTEAM_LLM_API_KEY="local-launcher-fixture",
        HEALTEAM_LLM_BASE_URL="http://127.0.0.1:9/v1",
        HEALTEAM_LLM_MODEL="launcher-not-a-paid-model",
    )
    return environment


def launch(tmp_path, *, console=False, overrides=None, caller_cwd=None):
    state_dir = tmp_path / "control"
    web_port = free_port()
    log = (tmp_path / "supervisor.log").open("w", encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW
    startup = None
    if console:
        flags = subprocess.CREATE_NEW_CONSOLE
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    environment = launcher_environment(tmp_path)
    environment.update(overrides or {})
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "scripts/local_app.py"),
                "start",
                "--no-browser",
                "--root",
                str(ROOT),
                "--state-dir",
                str(state_dir),
                "--web-port",
                str(web_port),
            ],
            cwd=caller_cwd or ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            startupinfo=startup,
        )
    except BaseException:
        log.close()
        raise
    try:
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log.flush()
                raise AssertionError((tmp_path / "supervisor.log").read_text(encoding="utf-8"))
            result = control(ROOT, state_dir)
            if result["status"] == "running":
                return process, log, state_dir, result
            time.sleep(0.1)
        raise AssertionError("Supervisor did not become ready")
    except BaseException as failure:
        try:
            stop_owned(process, log, state_dir)
        except Exception as cleanup_failure:
            failure.add_note(f"Owned launcher cleanup failed: {cleanup_failure}")
        raise


def stop_owned(process, log, state_dir):
    try:
        if process.poll() is None:
            try:
                result = control(ROOT, state_dir, "stop")
            except Exception:
                result = {}
            if result.get("status") == "stopping":
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    pass
    finally:
        try:
            if process.poll() is None:
                if os.name == "nt":
                    # This is the Popen handle retained by this test, never a PID
                    # obtained from the state file or another running service.
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                else:
                    process.kill()
            process.wait(timeout=10)
        finally:
            log.close()


@pytest.mark.skipif(os.name != "nt", reason="The launch helper uses the Windows foreground supervisor")
@pytest.mark.parametrize("failure_kind", ["control", "startup_timeout"])
def test_launch_failure_cleans_owned_process_and_preserves_log(tmp_path, monkeypatch, failure_kind):
    module = sys.modules[__name__]

    class OwnedProcess:
        pid = 987654321
        killed = False

        def poll(self):
            return 0 if self.killed else None

        def wait(self, *, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired("synthetic-owned-launcher", timeout)
            return 0

        def kill(self):
            self.killed = True

    process = OwnedProcess()
    opened = []

    def popen(_arguments, **keywords):
        opened.append(keywords["stdout"])
        keywords["stdout"].write("Synthetic launcher failure evidence\n")
        keywords["stdout"].flush()
        return process

    def status(_root, _state_dir, action="status"):
        if action == "stop" or failure_kind == "control":
            raise LocalRuntimeError("local_state_invalid")
        return {"status": "starting"}

    def kill_owned(arguments, **_keywords):
        assert arguments == ["taskkill", "/PID", str(process.pid), "/T", "/F"]
        process.kill()
        return subprocess.CompletedProcess(arguments, 0)

    times = iter([0, 0, 51])
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", kill_owned)
    monkeypatch.setattr(module, "control", status)
    monkeypatch.setattr(module, "free_port", lambda: 18002)
    monkeypatch.setattr(time, "monotonic", lambda: next(times))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    with pytest.raises(
        LocalRuntimeError if failure_kind == "control" else AssertionError,
        match="local_state_invalid" if failure_kind == "control" else "did not become ready",
    ):
        launch(tmp_path)
    assert process.killed and opened[0].closed
    assert (tmp_path / "supervisor.log").read_text(
        encoding="utf-8"
    ) == "Synthetic launcher failure evidence\n"


def test_production_start_duplicate_start_and_authenticated_stop(tmp_path, prepared_runtime):
    process, log, state_dir, state = launch(tmp_path)
    try:
        assert state["api_port"] == prepared_runtime["api_port"]
        with urllib.request.urlopen(f"http://127.0.0.1:{state['web_port']}/healthz", timeout=3) as response:
            assert json.load(response)["model_mode"] == "mock"
        for page in ("/", "/sessions/00000000-0000-4000-8000-000000000001"):
            with urllib.request.urlopen(f"http://127.0.0.1:{state['web_port']}{page}", timeout=5) as response:
                assert response.status == 200
        duplicate = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/local_app.py"),
                "start",
                "--no-browser",
                "--state-dir",
                str(state_dir),
            ],
            cwd=ROOT,
            env=launcher_environment(tmp_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert duplicate.returncode == 0 and "already_running" in duplicate.stdout
        assert state["pid"] == control(ROOT, state_dir)["pid"]
    finally:
        stop_owned(process, log, state_dir)
    assert control(ROOT, state_dir)["status"] == "stopped"
    wait_closed(state["api_port"], state["web_port"])
    assert load_manifest(ROOT)["version"] == prepared_runtime["version"]
    process, log, state_dir, restarted = launch(tmp_path)
    try:
        assert restarted["status"] == "running" and restarted["pid"] != state["pid"]
    finally:
        stop_owned(process, log, state_dir)
    assert load_manifest(ROOT)["version"] == prepared_runtime["version"]


def test_production_port_conflict_preserves_other_listener(tmp_path, prepared_runtime):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", prepared_runtime["api_port"]))
        blocker.listen()
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/local_app.py"),
                "start",
                "--no-browser",
                "--state-dir",
                str(tmp_path / "conflict"),
            ],
            cwd=ROOT,
            env=launcher_environment(tmp_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 1 and "local_port_in_use" in result.stdout
        assert blocker.fileno() >= 0 and port_open(prepared_runtime["api_port"])


def test_supervisor_force_kill_releases_api_and_owned_web(tmp_path, prepared_runtime):
    process, log, state_dir, state = launch(tmp_path)
    try:
        os.kill(state["pid"], signal.SIGTERM)
        process.wait(timeout=15)
        wait_closed(state["api_port"], state["web_port"])
        assert control(ROOT, state_dir)["status"] == "stopped"
    finally:
        stop_owned(process, log, state_dir)


def test_real_ctrl_c_stops_foreground_supervisor(tmp_path, prepared_runtime):
    process, log, state_dir, state = launch(tmp_path, console=True)
    try:
        sender = """
import ctypes, sys, time
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.FreeConsole()
assert k.AttachConsole(int(sys.argv[1]))
assert k.SetConsoleCtrlHandler(None, True)
assert k.GenerateConsoleCtrlEvent(0, 0)
time.sleep(0.3)
k.FreeConsole()
"""
        sent = subprocess.run(
            [sys.executable, "-c", sender, str(state["pid"])],
            capture_output=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert sent.returncode == 0
        process.wait(timeout=20)
        wait_closed(state["api_port"], state["web_port"])
        assert control(ROOT, state_dir)["status"] == "stopped"
    finally:
        stop_owned(process, log, state_dir)


def test_missing_live_key_still_opens_saved_history(tmp_path, prepared_runtime):
    process, log, state_dir, state = launch(tmp_path)
    try:
        data = json.dumps(
            {
                "client_request_id": "22d44867-46bc-4c4c-a010-685bb6d15dde",
                "case_id": "teen-truancy-01",
                "school": "satir",
                "mode": "training",
                "max_rounds": 1,
            }
        ).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{state['api_port']}/api/v1/sessions",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            saved = json.load(response)["session_id"]
    finally:
        stop_owned(process, log, state_dir)
    process, log, state_dir, state = launch(
        tmp_path, overrides={"HEALTEAM_MODEL_MODE": "live", "HEALTEAM_LLM_API_KEY": ""}
    )
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{state['web_port']}/api/v1/sessions/{saved}", timeout=5
        ) as response:
            assert json.load(response)["session_id"] == saved
        with urllib.request.urlopen(f"http://127.0.0.1:{state['web_port']}/", timeout=5) as response:
            assert response.status == 200
        with pytest.raises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(f"http://127.0.0.1:{state['web_port']}/readyz", timeout=5)
        assert failure.value.code == 503
        assert b"live_requires_api_key" in failure.value.read()
    finally:
        stop_owned(process, log, state_dir)
    wait_closed(state["api_port"], state["web_port"])


def test_explicit_root_controls_relative_database_from_another_cwd(tmp_path, prepared_runtime):
    from uuid import uuid4

    relative = Path(".cache") / ("launcher-cwd-" + uuid4().hex + ".sqlite3")
    expected = (ROOT / relative).resolve()
    assert expected.is_relative_to((ROOT / ".cache").resolve())
    process, log, state_dir, state = launch(
        tmp_path,
        caller_cwd=tmp_path,
        overrides={
            "HEALTEAM_DATABASE_URL": relative.as_posix(),
            "HEALTEAM_ROOT": str(tmp_path / "wrong-resource-root"),
        },
    )
    try:
        assert expected.is_file()
        assert not (tmp_path / relative).exists()
        with urllib.request.urlopen(f"http://127.0.0.1:{state['web_port']}/readyz", timeout=5) as response:
            assert response.status == 200
    finally:
        stop_owned(process, log, state_dir)
        for suffix in ("", ".lock", "-journal", "-wal", "-shm"):
            owned = expected.with_name(expected.name + suffix)
            assert owned.resolve().is_relative_to((ROOT / ".cache").resolve())
            owned.unlink(missing_ok=True)
