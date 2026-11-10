from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.main import create_app  # noqa: E402
from app.routers import sessions  # noqa: E402


@pytest.fixture(autouse=True)
def clean_model_environment(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith(("HEALTEAM_", "ANTHROPIC_", "OPENAI_", "LLM_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HEALTEAM_DATABASE_URL", str(tmp_path / "configuration.sqlite3"))


@pytest.mark.parametrize(
    "payload",
    [
        {"max_rounds": 0},
        {"max_rounds": 31},
        {"max_rounds": -1},
        {"max_rounds": True},
        {"school": "unknown"},
        {"case_id": "../outside"},
        {"case_id": "..\\outside"},
        {"mode": "training"},
    ],
)
def test_invalid_request_is_rejected_before_runner(monkeypatch, payload):
    calls = []

    async def forbidden_runner(**kwargs):
        calls.append(kwargs)
        raise AssertionError("invalid requests must not start a session")

    monkeypatch.setattr(sessions, "run_session", forbidden_runner)
    with TestClient(create_app(allow_legacy_demo=True)) as client:
        response = client.post("/api/v1/demo/sessions", json=payload)
    assert response.status_code == 422
    assert calls == []


def test_missing_case_is_404_before_stream(monkeypatch):
    async def forbidden_runner(**kwargs):
        raise AssertionError("missing case must not start a session")

    monkeypatch.setattr(sessions, "run_session", forbidden_runner)
    with TestClient(create_app(allow_legacy_demo=True)) as client:
        response = client.post("/api/v1/demo/sessions", json={"case_id": "missing-case"})
    assert response.status_code == 404
    assert "text/event-stream" not in response.headers.get("content-type", "")


def test_live_missing_credentials_is_not_a_mock_fallback(monkeypatch):
    monkeypatch.setenv("HEALTEAM_MODEL_MODE", "live")
    monkeypatch.setenv("HEALTEAM_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HEALTEAM_LLM_MODEL", "local-test-model")
    with TestClient(create_app(allow_legacy_demo=True)) as client:
        readiness = client.get("/readyz")
        response = client.post("/api/v1/demo/sessions", json={})
    assert readiness.status_code == 503
    assert response.status_code == 503
    assert "text/event-stream" not in response.headers.get("content-type", "")


def test_mock_readiness_exposes_mode_but_not_secrets(monkeypatch):
    secret = "do-not-disclose-this-value"
    monkeypatch.setenv("HEALTEAM_LLM_API_KEY", secret)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:" + secret + "@db/healteam")
    with TestClient(create_app(allow_legacy_demo=True)) as client:
        health = client.get("/healthz")
        readiness = client.get("/readyz")
    assert health.status_code == 200
    assert health.json()["model_mode"] == "mock"
    assert readiness.status_code == 200
    assert readiness.json()["model_mode"] == "mock"
    assert secret not in health.text + readiness.text


def test_invalid_case_schema_and_missing_resources_make_not_ready(tmp_path, monkeypatch):
    cases = tmp_path / "data" / "cases"
    cases.mkdir(parents=True)
    (tmp_path / "data" / "schools").mkdir()
    (tmp_path / "packages" / "prompts").mkdir(parents=True)
    (cases / "broken.json").write_text(json.dumps({"case_id": "broken", "family": []}), encoding="utf-8")
    monkeypatch.setenv("HEALTEAM_ROOT", str(tmp_path))
    with TestClient(create_app(allow_legacy_demo=True)) as client:
        readiness = client.get("/readyz")
        response = client.post("/api/v1/demo/sessions", json={"case_id": "broken"})
    assert readiness.status_code == 503
    assert response.status_code == 422
    assert str(tmp_path) not in readiness.text + response.text


def test_mock_import_does_not_require_provider_sdks():
    script = """
import builtins, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'anthropic', 'openai'}:
        raise AssertionError('provider SDK imported in mock mode')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from app.main import create_app
assert create_app(allow_legacy_demo=True)
"""
    env = dict(os.environ, HEALTEAM_MODEL_MODE="mock", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(REPO_ROOT / "apps" / "api"), str(REPO_ROOT)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_cors_is_local_only():
    with TestClient(create_app(allow_legacy_demo=True)) as client:
        response = client.options(
            "/api/v1/demo/sessions",
            headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"},
        )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_settings_unified_values_override_legacy_and_hide_key(monkeypatch):
    from app.settings import load_settings

    monkeypatch.setenv("HEALTEAM_MODEL_MODE", "live")
    monkeypatch.setenv("HEALTEAM_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HEALTEAM_LLM_API_KEY", "new-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "old-secret")
    monkeypatch.setenv("HEALTEAM_LLM_MODEL", "new-model")
    monkeypatch.setenv("LLM_MODEL", "old-model")
    settings = load_settings()
    assert settings.api_key.get_secret_value() == "new-secret"
    assert settings.model == "new-model"
    assert "new-secret" not in repr(settings)
    assert "new-secret" not in json.dumps(settings.model_dump())


def test_empty_unified_environment_values_allow_provider_legacy_fallback(monkeypatch):
    from app.settings import load_settings

    for key, value in {
        "HEALTEAM_MODEL_MODE": "live",
        "HEALTEAM_LLM_PROVIDER": "openai",
        "HEALTEAM_LLM_API_KEY": "",
        "OPENAI_API_KEY": "legacy-test-key",
        "HEALTEAM_LLM_MODEL": " ",
        "LLM_MODEL": "legacy-model",
    }.items():
        monkeypatch.setenv(key, value)
    settings = load_settings()
    assert settings.model == "legacy-model"
    assert settings.api_key.get_secret_value() == "legacy-test-key"


def test_invalid_provider_url_is_safe_configuration_error(monkeypatch):
    for key, value in {
        "HEALTEAM_MODEL_MODE": "live",
        "HEALTEAM_LLM_API_KEY": "test-only",
        "HEALTEAM_LLM_MODEL": "fake-model",
        "HEALTEAM_LLM_BASE_URL": "https://[bad-host",
    }.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(allow_legacy_demo=True)) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["issues"] == ["invalid_llm_base_url"]
    assert "bad-host" not in response.text


def test_mock_demo_stream_and_saved_report_are_explicitly_labelled(tmp_path, monkeypatch, caplog):
    for relative in ("data/cases", "data/schools", "packages/prompts"):
        shutil.copytree(REPO_ROOT / relative, tmp_path / relative)
    monkeypatch.setenv("HEALTEAM_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:must-not-be-logged@db/healteam")
    with TestClient(create_app(allow_legacy_demo=True)) as client:
        response = client.post("/api/v1/demo/sessions", json={"max_rounds": 1})
    assert response.status_code == 200
    assert '"mode": "demo"' in response.text
    assert '"model_mode": "mock"' in response.text
    assert "event: complete" in response.text
    assert "离线流程演示报告 / 固定占位回复，仅验证流程，不代表训练评价" in response.text
    reports = list((tmp_path / "data" / "reports").glob("*.md"))
    assert len(reports) == 1
    assert reports[0].read_text(encoding="utf-8").startswith("# 离线流程演示报告")
    assert str(tmp_path) not in response.text
    assert "must-not-be-logged" not in caplog.text


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_provider_adapter_uses_configuration_without_network(monkeypatch, provider):
    import httpx
    from app.services.llm import build_llm_client
    from app.settings import load_settings

    for key, value in {
        "HEALTEAM_MODEL_MODE": "live",
        "HEALTEAM_LLM_PROVIDER": provider,
        "HEALTEAM_LLM_API_KEY": "test-only",
        "HEALTEAM_LLM_MODEL": "fake-model",
        "HEALTEAM_LLM_BASE_URL": "https://local.example/v1",
        "HEALTEAM_LLM_TEMPERATURE": "0.2",
        "HEALTEAM_LLM_MAX_TOKENS": "64",
        "HEALTEAM_LLM_TIMEOUT_SECONDS": "3",
    }.items():
        monkeypatch.setenv(key, value)
    sent = []

    async def fake_post(self, url, **kwargs):
        sent.append((str(url), kwargs, self.timeout.read))
        payload = (
            {"choices": [{"message": {"content": "configured reply"}}]}
            if provider == "openai"
            else {"content": [{"type": "text", "text": "configured reply"}]}
        )
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = build_llm_client(load_settings())
    reply = asyncio.run(
        client.chat(system="role", messages=[{"role": "user", "content": "hello"}], max_tokens=2048)
    )
    assert reply == "configured reply"
    assert sent[0][1]["json"]["model"] == "fake-model"
    assert sent[0][1]["json"]["temperature"] == 0.2
    assert sent[0][1]["json"]["max_tokens"] == 64
    assert sent[0][2] == 3


def test_live_provider_failure_does_not_complete_with_placeholder_report(monkeypatch):
    import httpx

    for key, value in {
        "HEALTEAM_MODEL_MODE": "live",
        "HEALTEAM_LLM_PROVIDER": "openai",
        "HEALTEAM_LLM_API_KEY": "test-only",
        "HEALTEAM_LLM_MODEL": "fake-model",
    }.items():
        monkeypatch.setenv(key, value)
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        return httpx.Response(
            401, json={"error": "private upstream message"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with TestClient(create_app(allow_legacy_demo=True)) as client:
        response = client.post("/api/v1/demo/sessions", json={"max_rounds": 1})
    assert "event: error" in response.text
    assert "event: complete" not in response.text
    assert "event: report" not in response.text
    assert "private upstream message" not in response.text
    assert len(calls) == 1


def test_disconnected_stream_cleans_up_queue_waiter(monkeypatch):
    async def waiting_runner(**kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(sessions, "run_session", waiting_runner)

    async def scenario():
        before = set(asyncio.all_tasks())
        response = await sessions.create_session(
            sessions.SessionCreate(), Request({"type": "http", "app": create_app(allow_legacy_demo=True)})
        )
        consumer = asyncio.create_task(response.body_iterator.__anext__())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        assert set(asyncio.all_tasks()) - before == set()

    asyncio.run(scenario())
