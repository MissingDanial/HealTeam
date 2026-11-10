"""Build/package safety, manifest integrity and relocatable prebuilt delivery."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import types
import zipfile
from pathlib import Path

import pytest

from scripts import package_local, prepare_local


@pytest.fixture
def project(tmp_path, repo_root):
    root = tmp_path / "HealTeam-main/healteam"
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nversion = "0.6.0"\n', encoding="utf-8")
    (root / "README.md").write_text("Local app", encoding="utf-8")
    (root / "requirements.lock").write_text("# locked dependencies", encoding="utf-8")
    web = root / "apps/web"
    web.mkdir(parents=True)
    (web / "package-lock.json").write_text("{}", encoding="utf-8")
    (web / "node_modules/next/dist/bin").mkdir(parents=True)
    (web / "node_modules/next/dist/bin/next").write_text("next", encoding="utf-8")
    (root / "packages/memory").mkdir(parents=True)
    shutil.copyfile(repo_root / "packages/memory/runtime_lease.py", root / "packages/memory/runtime_lease.py")
    return root


def build_files(root):
    web = root / "apps/web"
    for relative, content in {
        ".next/standalone/server.js": "process.stdout.write('fixture');",
        ".next/standalone/.next/required-server-files.json": '{"config":{"experimental":{"isrFlushToDisk":false}}}',
        ".next/standalone/node_modules/next/package.json": '{"name":"next"}',
        ".next/static/chunks/app.js": "console.log('public bundle');",
        "public/favicon.svg": "<svg></svg>",
    }.items():
        path = web / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def mock_tools(monkeypatch):
    monkeypatch.setattr(
        prepare_local,
        "node_tools",
        lambda **_kwargs: ("node", ["node", "npm-cli.js"], {"major": 24, "platform": "win32", "arch": "x64"}),
    )


def prepared(project, monkeypatch):
    mock_tools(monkeypatch)
    build_files(project)
    monkeypatch.setattr(prepare_local, "_run", lambda *_args, **_kwargs: None)
    return prepare_local.prepare(project, api_url="http://127.0.0.1:8000")


def test_build_environment_excludes_backend_and_public_secret_variables():
    env = prepare_local.clean_environment(
        {
            "PATH": "node-path",
            "SystemRoot": "Windows",
            "HEALTEAM_LLM_API_KEY": "secret",
            "DEEPSEEK_API_KEY": "secret",
            "OPENAI_API_KEY": "secret",
            "ANTHROPIC_API_KEY": "secret",
            "NEXT_PUBLIC_PRIVATE_TOKEN": "secret",
            "NODE_OPTIONS": "--require unsafe.js",
            "PYTHONPATH": "unsafe",
        }
    )
    assert env["PATH"] == "node-path" and env["SystemRoot"] == "Windows"
    assert "secret" not in env.values()
    assert "NODE_OPTIONS" not in env and "PYTHONPATH" not in env


def test_prebuilt_validation_does_not_require_npm(tmp_path, monkeypatch):
    node = tmp_path / "node.exe"
    node.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(prepare_local.shutil, "which", lambda name: str(node) if name == "node" else None)
    monkeypatch.setattr(
        prepare_local.subprocess,
        "run",
        lambda *_args, **_kwargs: types.SimpleNamespace(
            stdout='{"major":24,"platform":"win32","arch":"x64"}'
        ),
    )
    assert prepare_local.node_tools(require_npm=False)[1] == []
    with pytest.raises(prepare_local.PreparationError, match="npm_cli_not_found"):
        prepare_local.node_tools()


def test_default_cli_uses_the_managed_api_port(project, monkeypatch):
    captured = []

    def prepare(root, *, api_url, install, use_prebuilt):
        captured.append(api_url)
        return {"version": "0.6.0", "api_internal_url": api_url, "files": {}}

    monkeypatch.setattr(prepare_local, "prepare", prepare)
    monkeypatch.setattr(prepare_local.sys, "argv", ["prepare_local.py", "--root", str(project)])
    prepare_local.main()
    assert captured == ["http://127.0.0.1:18000"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:8000",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://user:key@127.0.0.1:8000",
        "http://127.0.0.1:8000/path",
        "http://127.0.0.1:8000?key=value",
        "http://127.0.0.1:0",
    ],
)
def test_baked_proxy_cannot_target_remote_or_ambiguous_service(url):
    with pytest.raises(prepare_local.PreparationError):
        prepare_local.normalize_api_url(url)


def test_prepare_copies_static_and_public_with_a_complete_manifest(project, monkeypatch):
    manifest = prepared(project, monkeypatch)
    assert manifest["version"] == "0.6.0" and manifest["node_major"] == 24
    assert manifest["api_internal_url"] == "http://127.0.0.1:8000"
    assert {"web/server.js", "web/.next/static/chunks/app.js", "web/public/favicon.svg"} <= set(
        manifest["files"]
    )
    assert prepare_local.verified_runtime(project)[0] == manifest
    relocated = project.parent.parent / "relocated/HealTeam-main/healteam"
    shutil.copytree(project, relocated)
    assert prepare_local.verified_runtime(relocated)[0]["files"] == manifest["files"]


def test_prebuilt_install_installs_python_only_and_never_calls_build(project, monkeypatch):
    manifest = prepared(project, monkeypatch)
    calls = []
    monkeypatch.setattr(
        prepare_local, "install_dependencies", lambda root, npm, *, frontend=True: calls.append(frontend)
    )
    monkeypatch.setattr(
        prepare_local, "_run", lambda *_args, **_kwargs: pytest.fail("prebuilt install must not build")
    )
    result = prepare_local.prepare(
        project, api_url=manifest["api_internal_url"], install=True, use_prebuilt=True
    )
    assert result == manifest and calls == [False]


@pytest.mark.parametrize("flush_to_disk", [True, None])
def test_mutable_next_runtime_is_rejected_before_publication(project, monkeypatch, flush_to_disk):
    mock_tools(monkeypatch)
    build_files(project)
    path = project / "apps/web/.next/standalone/.next/required-server-files.json"
    path.write_text(
        json.dumps({"config": {"experimental": {"isrFlushToDisk": flush_to_disk}}}), encoding="utf-8"
    )
    monkeypatch.setattr(prepare_local, "_run", lambda *_args, **_kwargs: None)
    with pytest.raises(prepare_local.PreparationError, match="disk_cache_must_be_disabled"):
        prepare_local.prepare(project, api_url="http://127.0.0.1:18000")
    assert not (project / ".local/runtime").exists()


def test_failed_build_keeps_the_old_runtime_and_releases_build_lock(project, monkeypatch):
    before = prepared(project, monkeypatch)

    def fail(*_args, **_kwargs):
        raise prepare_local.PreparationError("fixture_failure")

    monkeypatch.setattr(prepare_local, "_run", fail)
    with pytest.raises(prepare_local.PreparationError, match="fixture_failure"):
        prepare_local.prepare(project, api_url="http://127.0.0.1:8000")
    assert prepare_local.verified_runtime(project)[0] == before
    lease = prepare_local._runtime_lease(project)
    lease.acquire()
    lease.release()


def test_running_runtime_cannot_be_rebuilt(project, monkeypatch):
    prepared(project, monkeypatch)
    lease = prepare_local._runtime_lease(project)
    lease.acquire()
    try:
        with pytest.raises(prepare_local.PreparationError, match="stop_local_application"):
            prepare_local.prepare(project, api_url="http://127.0.0.1:8000")
    finally:
        lease.release()


@pytest.mark.parametrize(
    "mutation", ["tamper", "extra", "path", "version", "platform", "arch", "shape", "private"]
)
def test_modified_or_private_runtime_is_rejected(project, monkeypatch, mutation):
    prepared(project, monkeypatch)
    runtime = project / ".local/runtime"
    path = runtime / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "tamper":
        (runtime / "web/server.js").write_text("changed", encoding="utf-8")
    elif mutation == "extra":
        (runtime / "web/extra.txt").write_text("unlisted", encoding="utf-8")
    elif mutation == "path":
        manifest["files"]["../outside"] = "0" * 64
    elif mutation == "version":
        manifest["version"] = "0.5.0"
    elif mutation == "platform":
        manifest["platform"] = "../../outside"
    elif mutation == "arch":
        manifest["arch"] = "unknown"
    elif mutation == "shape":
        manifest = []
    else:
        (runtime / "web/.env").write_text("KEY=private", encoding="utf-8")
        manifest["files"]["web/.env"] = hashlib.sha256(b"KEY=private").hexdigest()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(prepare_local.PreparationError):
        prepare_local.verified_runtime(project)


def test_frontend_env_file_is_rejected_before_build(project, monkeypatch):
    mock_tools(monkeypatch)
    (project / "apps/web/.env.local").write_text("KEY=do-not-embed", encoding="utf-8")
    with pytest.raises(prepare_local.PreparationError, match="frontend_env"):
        prepare_local.prepare(project, api_url="http://127.0.0.1:8000")


def test_windows_reparse_detection_works_without_python312_junction_api(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "lstat", lambda _path: types.SimpleNamespace(st_file_attributes=0x400))
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    assert prepare_local.is_link(tmp_path / "junction")


def test_materialization_rejects_external_link_and_link_cycles(tmp_path):
    source, outside = tmp_path / "source", tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    (outside / "private.txt").write_text("private", encoding="utf-8")

    def link(target, path):
        try:
            os.symlink(target, path, target_is_directory=True)
        except OSError:
            powershell = shutil.which("powershell") or shutil.which("pwsh")
            if os.name != "nt" or powershell is None:
                pytest.skip("directory link creation unavailable")
            environment = prepare_local.clean_environment()
            environment.update(HEALTEAM_TEST_LINK=str(path), HEALTEAM_TEST_TARGET=str(target))
            result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "New-Item -ItemType Junction -Path $env:HEALTEAM_TEST_LINK -Target $env:HEALTEAM_TEST_TARGET | Out-Null",
                ],
                env=environment,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                pytest.skip("junction creation unavailable")

    link(outside, source / "escape")
    with pytest.raises(prepare_local.PreparationError, match="escaped"):
        prepare_local.materialize_tree(source, tmp_path / "copy", allowed_roots=(source,))
    if (source / "escape").is_symlink():
        (source / "escape").unlink()
    else:
        (source / "escape").rmdir()  # Remove this owned junction, never its target.
    link(source, source / "loop")
    with pytest.raises(prepare_local.PreparationError, match="cycle"):
        prepare_local.materialize_tree(source, tmp_path / "copy2", allowed_roots=(source,))


def test_local_archive_includes_prebuilt_assets_but_no_data_or_configuration(project, tmp_path, monkeypatch):
    prepared(project, monkeypatch)
    (project / ".env").write_text("KEY=PRIVATE_DONT_EMBED", encoding="utf-8")
    (project / "backups").mkdir()
    (project / "backups/snapshot.zip").write_bytes(b"PRIVATE_DONT_EMBED")
    (project / "data").mkdir()
    (project / "data/user.sqlite3").write_bytes(b"PRIVATE_DONT_EMBED")
    monkeypatch.setattr(
        package_local.package_source, "REQUIRED", ("healteam/README.md", "healteam/requirements.lock")
    )
    output = tmp_path / "output/local.zip"
    result = package_local.package(project, output)
    with zipfile.ZipFile(output) as bundle:
        names = bundle.namelist()
        assert "HealTeam-main/healteam/.local/runtime/web/.next/static/chunks/app.js" in names
        assert not any(b"PRIVATE_DONT_EMBED" in bundle.read(name) for name in names)
        assert not any(".venv" in name or "/backups/" in name or name.endswith(".env") for name in names)
    assert output.with_suffix(".zip.sha256").is_file() and result["runtime_files"] >= 4
    with pytest.raises(prepare_local.PreparationError, match="outside_source"):
        package_local.package(project, project / "bad.zip")


def test_runtime_change_during_packaging_never_publishes_a_mixed_archive(project, tmp_path, monkeypatch):
    prepared(project, monkeypatch)
    monkeypatch.setattr(package_local.package_source, "REQUIRED", ("healteam/README.md",))
    original_write = zipfile.ZipFile.write

    def mutate_then_write(bundle, filename, arcname=None, **kwargs):
        if str(arcname).endswith("/.local/runtime/web/server.js"):
            Path(filename).write_text("late change", encoding="utf-8")
        return original_write(bundle, filename, arcname=arcname, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", mutate_then_write)
    output = tmp_path / "output/mixed.zip"
    with pytest.raises(prepare_local.PreparationError, match="runtime_changed_during_packaging"):
        package_local.package(project, output)
    assert not output.exists()
