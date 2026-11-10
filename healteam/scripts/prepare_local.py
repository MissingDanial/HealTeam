"""Prepare a relocatable production web runtime without model credentials."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "healteam-local-runtime-v1"
DEFAULT_API_PORT = 18000
OS_ENV = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "LANG",
    "LC_ALL",
    "TERM",
    "HOME",
}


class PreparationError(RuntimeError):
    pass


def clean_environment(environment: dict[str, str] | None = None) -> dict[str, str]:
    """Never pass HEALTEAM/LLM/provider/NEXT_PUBLIC variables into a build."""
    source = os.environ if environment is None else environment
    result = {key: value for key, value in source.items() if key.upper() in OS_ENV}
    result.update(NEXT_TELEMETRY_DISABLED="1", PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return result


def normalize_api_url(value: str) -> str:
    if not isinstance(value, str) or any(ord(character) <= 32 for character in value):
        raise PreparationError("api_url_must_be_explicit_loopback_port")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
        ):
            raise ValueError
        return f"http://127.0.0.1:{parsed.port}"
    except ValueError:
        raise PreparationError("api_url_must_be_explicit_loopback_port") from None


def is_link(path: Path) -> bool:
    """Recognize Windows junction/reparse entries on Python 3.11 too."""
    try:
        attributes = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        getattr(attributes, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _inside(path: Path, parent: Path) -> Path:
    resolved, boundary = path.resolve(), parent.resolve()
    if not resolved.is_relative_to(boundary) or resolved == boundary:
        raise PreparationError("runtime_path_outside_local_directory")
    return resolved


def _remove_tree(path: Path, parent: Path) -> None:
    target = _inside(path, parent)
    if is_link(path):
        raise PreparationError("linked_runtime_directory")
    if target.exists():
        shutil.rmtree(target)


def materialize_tree(source: Path, destination: Path, *, allowed_roots: tuple[Path, ...]) -> None:
    """Dereference Next's Windows junctions, rejecting escapes and cycles."""
    roots = tuple(path.resolve() for path in allowed_roots)

    def copy(current: Path, target: Path, ancestors: frozenset[Path]) -> None:
        resolved = current.resolve(strict=True)
        if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
            raise PreparationError("standalone_link_escaped_build_sources")
        if resolved.is_dir():
            if resolved in ancestors:
                raise PreparationError("standalone_link_cycle")
            target.mkdir(parents=True, exist_ok=True)
            for item in sorted(resolved.iterdir()):
                copy(item, target / item.name, ancestors | {resolved})
        elif resolved.is_file():
            if current.name.lower().startswith(".env") and current.name.lower() != ".env.example":
                raise PreparationError("environment_file_in_standalone")
            if current.name.lower().endswith((".sqlite3", ".sqlite", ".db", ".pem", ".key")):
                raise PreparationError("private_data_in_standalone")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved, target)
        else:
            raise PreparationError("unsupported_standalone_file")

    copy(source, destination, frozenset())


def runtime_files(runtime: Path) -> dict[str, str]:
    result = {}
    for current, directories, files in os.walk(runtime, followlinks=False):
        for name in directories:
            path = Path(current) / name
            if is_link(path):
                raise PreparationError("linked_runtime_directory")
        for name in sorted(files):
            path = Path(current) / name
            if is_link(path):
                raise PreparationError("linked_runtime_file")
            relative = path.relative_to(runtime).as_posix()
            if relative != "manifest.json":
                result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(result.items()))


def immutable_web_runtime(web: Path) -> None:
    """Next page requests must not create unlisted route-cache files."""
    try:
        settings = json.loads((web / ".next/required-server-files.json").read_text(encoding="utf-8"))
        immutable = settings["config"]["experimental"]["isrFlushToDisk"] is False
    except (OSError, ValueError, KeyError, TypeError):
        immutable = False
    if not immutable:
        raise PreparationError("standalone_disk_cache_must_be_disabled")


def verified_runtime(root: Path) -> tuple[dict, dict[str, Path]]:
    runtime = root / ".local/runtime"
    if is_link(runtime) or is_link(root / ".local"):
        raise PreparationError("linked_runtime_directory")
    manifest_path = runtime / "manifest.json"
    if is_link(manifest_path):
        raise PreparationError("linked_runtime_file")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != FORMAT
        or manifest.get("entry") != "web/server.js"
    ):
        raise PreparationError("invalid_runtime_manifest")
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if (
        not isinstance(version, str)
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version)
        or manifest.get("version") != version
    ):
        raise PreparationError("runtime_version_requires_rebuild")
    if type(manifest.get("node_major")) is not int or manifest["node_major"] not in {22, 24}:
        raise PreparationError("unsupported_runtime_node")
    if manifest.get("platform") not in {"win32", "linux", "darwin"} or manifest.get("arch") not in {
        "x64",
        "arm64",
    }:
        raise PreparationError("unsupported_runtime_platform")
    normalize_api_url(manifest["api_internal_url"])
    files = manifest.get("files")
    if not isinstance(files, dict) or not 1 <= len(files) <= 50000:
        raise PreparationError("invalid_runtime_files")
    for name, digest in files.items():
        path = PurePosixPath(name)
        if (
            not isinstance(name, str)
            or "\\" in name
            or ":" in name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or path.parts[0] != "web"
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise PreparationError("invalid_runtime_file_path")
        if path.name.lower().startswith(".env") and path.name.lower() != ".env.example":
            raise PreparationError("private_runtime_file")
        if path.name.lower().endswith((".sqlite3", ".sqlite", ".db", ".pem", ".key")):
            raise PreparationError("private_runtime_file")
    actual = runtime_files(runtime)
    if actual != files:
        raise PreparationError("runtime_integrity_check_failed")
    if "web/server.js" not in files or not any(name.startswith("web/.next/static/") for name in files):
        raise PreparationError("standalone_build_incomplete")
    immutable_web_runtime(runtime / "web")
    paths = {name: runtime / name for name in files}
    if sum(path.stat().st_size for path in paths.values()) > 1_073_741_824:
        raise PreparationError("runtime_too_large")
    paths["manifest.json"] = manifest_path
    return manifest, paths


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> None:
    subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def node_tools(*, require_npm: bool = True) -> tuple[str, list[str], dict]:
    node = shutil.which("node")
    if node is None:
        raise PreparationError("node_22_or_24_required")
    result = subprocess.run(
        [
            node,
            "-p",
            "JSON.stringify({major:Number(process.versions.node.split('.')[0]),platform:process.platform,arch:process.arch})",
        ],
        env=clean_environment(),
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(result.stdout)
    if info["major"] not in {22, 24}:
        raise PreparationError("node_22_or_24_required")
    if not require_npm:
        return node, [], info
    candidates = [Path(node).resolve().parent / "node_modules/npm/bin/npm-cli.js"]
    npm = shutil.which("npm")
    if npm:
        candidates.append(Path(npm).resolve())
    script = next((path for path in candidates if path.is_file() and path.suffix == ".js"), None)
    if script is None:
        raise PreparationError("npm_cli_not_found")
    return node, [node, str(script)], info


def install_dependencies(root: Path, npm: list[str], *, frontend: bool = True) -> None:
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    environment = clean_environment()
    if not python.is_file():
        if not (3, 11) <= sys.version_info[:2] < (3, 13):
            raise PreparationError("python_311_or_312_required")
        _run([sys.executable, "-m", "venv", str(root / ".venv")], cwd=root, environment=environment)
    uv = shutil.which("uv")
    if uv:
        installer = [uv, "--cache-dir", str(root / ".cache/uv"), "pip", "install", "--python", str(python)]
    else:
        installer = [str(python), "-m", "pip", "install"]
    _run([*installer, "-r", str(root / "requirements.lock")], cwd=root, environment=environment)
    _run([*installer, "--no-deps", "-e", str(root)], cwd=root, environment=environment)
    if frontend:
        _run([*npm, "ci", "--no-fund", "--no-audit"], cwd=root / "apps/web", environment=environment)


def _runtime_lease(root: Path):
    # This source module uses only stdlib and can run before editable install.
    spec = importlib.util.spec_from_file_location(
        "healteam_prepare_lease", root / "packages/memory/runtime_lease.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RuntimeLease(root / ".local/runtime")


def prepare(root: Path, *, api_url: str, install: bool = False, use_prebuilt: bool = False) -> dict:
    root = root.resolve()
    api_url = normalize_api_url(api_url)
    web = root / "apps/web"
    _inside(web, root)
    if not (root / "pyproject.toml").is_file() or not (web / "package-lock.json").is_file():
        raise PreparationError("source_root_required")
    for path in web.glob(".env*"):
        if path.name != ".env.example":
            raise PreparationError("remove_frontend_env_files_before_build")
    _, npm, info = node_tools(require_npm=not use_prebuilt)
    local = root / ".local"
    if is_link(local):
        raise PreparationError("linked_runtime_directory")
    local.mkdir(exist_ok=True)
    lease = _runtime_lease(root)
    try:
        lease.acquire()
    except RuntimeError:
        raise PreparationError("stop_local_application_before_preparing") from None
    stage = None
    try:
        if use_prebuilt:
            manifest, _ = verified_runtime(root)
            if (
                any(manifest.get(key) != info[key] for key in ("platform", "arch"))
                or manifest["node_major"] != info["major"]
            ):
                raise PreparationError("prebuilt_runtime_platform_or_node_mismatch")
            if manifest["api_internal_url"] != api_url:
                raise PreparationError("prebuilt_api_url_requires_rebuild")
            if install:
                install_dependencies(root, npm, frontend=False)
            return manifest
        if install:
            install_dependencies(root, npm)
        if not (web / "node_modules/next/dist/bin/next").is_file():
            raise PreparationError("dependencies_missing_run_prepare_with_install")
        environment = clean_environment()
        environment["HEALTEAM_API_INTERNAL_URL"] = api_url
        _run([*npm, "run", "build"], cwd=web, environment=environment)
        standalone = web / ".next/standalone"
        if not (standalone / "server.js").is_file() or not (web / ".next/static").is_dir():
            raise PreparationError("standalone_build_incomplete")
        immutable_web_runtime(standalone)
        stage = Path(tempfile.mkdtemp(prefix="runtime-stage-", dir=local))
        allowed = (standalone, web / "node_modules", web / ".next", web / "public")
        if any(not path.resolve().is_relative_to(web.resolve()) for path in allowed):
            raise PreparationError("build_sources_escaped_web_directory")
        materialize_tree(standalone, stage / "web", allowed_roots=allowed)
        materialize_tree(web / ".next/static", stage / "web/.next/static", allowed_roots=allowed)
        materialize_tree(web / "public", stage / "web/public", allowed_roots=allowed)
        manifest = {
            "format": FORMAT,
            "version": tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
                "version"
            ],
            "node_major": info["major"],
            "platform": info["platform"],
            "arch": info["arch"],
            "python_requires": ">=3.11,<3.13",
            "api_internal_url": api_url,
            "entry": "web/server.js",
            "prepared_at": datetime.now(UTC).isoformat(),
            "files": runtime_files(stage),
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        destination = local / "runtime"
        previous = None
        if destination.exists():
            _inside(destination, local)
            if is_link(destination):
                raise PreparationError("linked_runtime_directory")
            previous = local / f"runtime-previous-{os.getpid()}"
            if previous.exists():
                raise PreparationError("previous_runtime_cleanup_required")
            destination.rename(previous)
        try:
            stage.rename(destination)
            stage = None
        except BaseException:
            if previous is not None:
                previous.rename(destination)
            raise
        if previous is not None:
            _remove_tree(previous, local)
        return manifest
    finally:
        if stage is not None and stage.exists():
            _remove_tree(stage, local)
        lease.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--api-url", default=None)
    group.add_argument("--api-port", type=int, default=None)
    parser.add_argument("--install", action="store_true", help="Install locked dependencies first")
    parser.add_argument(
        "--use-prebuilt", action="store_true", help="Verify bundled web files; --install installs Python only"
    )
    args = parser.parse_args()
    api_url = (
        args.api_url or f"http://127.0.0.1:{args.api_port if args.api_port is not None else DEFAULT_API_PORT}"
    )
    if args.use_prebuilt and args.api_url is None and args.api_port is None:
        try:
            api_url = json.loads((args.root / ".local/runtime/manifest.json").read_text(encoding="utf-8"))[
                "api_internal_url"
            ]
        except (OSError, ValueError, KeyError):
            raise SystemExit("预构建文件缺失或无效，请先运行安装与构建。") from None
    try:
        result = prepare(args.root, api_url=api_url, install=args.install, use_prebuilt=args.use_prebuilt)
    except (PreparationError, OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
        code = str(exc) if isinstance(exc, PreparationError) else "local_preparation_failed"
        print(f"无法完成安装与构建：{code}", file=sys.stderr)
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "status": "prepared",
                "version": result["version"],
                "api_internal_url": result["api_internal_url"],
                "runtime_files": len(result["files"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
