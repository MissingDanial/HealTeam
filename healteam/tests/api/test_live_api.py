"""Real local HTTP provider -> durable API -> report, without paid services."""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

from tests.providers.fake_server import fake_provider


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    import os

    for name in tuple(os.environ):
        if name.startswith(("HEALTEAM_", "LLM_", "ANTHROPIC_", "OPENAI_", "DEEPSEEK_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HEALTEAM_DATABASE_URL", str(tmp_path / "live.sqlite3"))
    monkeypatch.setenv("HEALTEAM_MODEL_MODE", "live")
    monkeypatch.setenv("HEALTEAM_LLM_API_KEY", "local-fixture-key-s3")
    monkeypatch.setenv("HEALTEAM_LLM_MODEL", "healteam-local-fixture")


def command(**changes):
    return {
        "client_request_id": str(uuid4()),
        "case_id": "teen-truancy-01",
        "school": "satir",
        "mode": "training",
        "max_rounds": 1,
        **changes,
    }


def message():
    return {"client_message_id": str(uuid4()), "expected_turn": 1, "content": "请爸爸先说说你的感受。"}


def wait_for(client, session_id, *states):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 200, response.text
        view = response.json()
        if view["state"] in states:
            return view
        time.sleep(0.01)
    raise AssertionError(f"Session failed to reach {states}: {view}")


@pytest.mark.parametrize("provider", ["anthropic", "openai", "deepseek"])
@pytest.mark.parametrize("school", ["structural", "bowen", "satir"])
def test_live_round_report_and_usage_across_providers_and_schools(monkeypatch, provider, school):
    with fake_provider() as (fixture, base):
        monkeypatch.setenv("HEALTEAM_LLM_PROVIDER", provider)
        monkeypatch.setenv("HEALTEAM_LLM_BASE_URL", base)
        with TestClient(create_app(training_delay=0.001)) as client:
            session_id = client.post("/api/v1/sessions", json=command(school=school)).json()["session_id"]
            assert fixture.calls == 0
            sent = message()
            assert client.post(f"/api/v1/sessions/{session_id}/messages", json=sent).status_code == 202
            complete = wait_for(client, session_id, "completed")
            assert complete["model_mode"] == "live" and complete["provider"] == provider
            assert complete["usage"]["requests"] == fixture.calls == 2
            assert complete["usage"]["unknown_requests"] == complete["usage"]["reserved_tokens"] == 0
            spoken = [event for event in complete["events"] if event["type"] == "utterance"]
            assert [event["actor"] for event in spoken] == ["trainee", "father"]
            assert not any(event["visibility"] == "internal" for event in complete["events"])
            report = client.get(f"/api/v1/reports/{session_id}").json()
            assert report["kind"] == "ai_review" and "未经专家校准" in report["content"]
            visible = {event["seq"] for event in spoken}
            assert all(
                set(point["evidence_seq"]) <= visible for point in report["structured"]["observations"]
            )
            assert client.post(f"/api/v1/sessions/{session_id}/messages", json=sent).status_code == 202
            assert fixture.calls == 2
            responses = [
                client.get(path)
                for path in (
                    "/healthz",
                    "/readyz",
                    "/api/v1/sessions",
                    f"/api/v1/sessions/{session_id}",
                    f"/api/v1/reports/{session_id}",
                    f"/api/v1/sessions/{session_id}/events",
                )
            ]
            public = "".join(response.text for response in responses)
            assert "local-fixture-key-s3" not in public and base not in public
            assert "model_call_started" not in public and "live_resources" not in public


@pytest.mark.parametrize(
    "mode,code",
    [
        ("auth_error", "llm_http_401"),
        ("rate_limit", "llm_http_429"),
        ("server_error", "llm_http_500"),
        ("wrong_actor", "model_role_output_invalid"),
        ("invalid_json", "model_output_invalid"),
        ("truncated", "llm_truncated_response"),
    ],
)
def test_provider_failures_are_durable_and_do_not_fall_back_to_mock(monkeypatch, mode, code):
    with fake_provider() as (fixture, base):
        monkeypatch.setenv("HEALTEAM_LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("HEALTEAM_LLM_BASE_URL", base)
        fixture.configure(mode)
        with TestClient(create_app(training_delay=0.001)) as client:
            session_id = client.post("/api/v1/sessions", json=command()).json()["session_id"]
            client.post(f"/api/v1/sessions/{session_id}/messages", json=message())
            failed = wait_for(client, session_id, "failed")
            assert failed["error_code"] == code
            assert fixture.calls == failed["usage"]["requests"] == 1
            assert not [
                event
                for event in failed["events"]
                if event["type"] == "utterance" and event["source"] == "ai"
            ]
            assert client.get(f"/api/v1/reports/{session_id}").json()["content"] is None


def test_invalid_review_evidence_can_retry_without_repeating_conversation(monkeypatch):
    with fake_provider() as (fixture, base):
        monkeypatch.setenv("HEALTEAM_LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("HEALTEAM_LLM_BASE_URL", base)
        with TestClient(create_app(training_delay=0.001)) as client:
            session_id = client.post("/api/v1/sessions", json=command(max_rounds=3)).json()["session_id"]
            client.post(f"/api/v1/sessions/{session_id}/messages", json=message())
            waiting = wait_for(client, session_id, "awaiting_input")
            fixture.configure("bad_evidence")
            client.post(f"/api/v1/sessions/{session_id}/finish")
            failed = wait_for(client, session_id, "failed")
            assert failed["error_code"] == "model_evidence_invalid"
            assert failed["usage"]["unknown_requests"] == 0
            fixture.configure("normal")
            assert client.post(f"/api/v1/reports/{session_id}/retry").status_code == 202
            complete = wait_for(client, session_id, "completed")

            def spoken(view):
                return [event for event in view["events"] if event["type"] == "utterance"]

            assert spoken(complete) == spoken(waiting)
            assert complete["usage"]["requests"] == fixture.calls == 3


def test_missing_key_does_not_hide_saved_history_and_cannot_create_live(monkeypatch):
    with fake_provider() as (_fixture, base):
        monkeypatch.setenv("HEALTEAM_LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("HEALTEAM_LLM_BASE_URL", base)
        with TestClient(create_app()) as client:
            session_id = client.post("/api/v1/sessions", json=command()).json()["session_id"]
            client.post(f"/api/v1/sessions/{session_id}/cancel")
        monkeypatch.delenv("HEALTEAM_LLM_API_KEY")
        with TestClient(create_app()) as client:
            assert client.get("/readyz").status_code == 503
            assert client.get(f"/api/v1/sessions/{session_id}").status_code == 200
            assert client.get("/api/v1/sessions").json()["total"] == 1
            response = client.post("/api/v1/sessions", json=command())
            assert (
                response.status_code == 503 and response.json()["detail"]["code"] == "live_requires_api_key"
            )
