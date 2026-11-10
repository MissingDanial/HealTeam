"""S1 public API stories against a real, temporary SQLite file."""

from __future__ import annotations

import json
import sqlite3
import time
from uuid import uuid4

import pytest
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTEAM_MODEL_MODE", "mock")
    monkeypatch.setenv("HEALTEAM_DATABASE_URL", str(tmp_path / "default.sqlite3"))


def command(**changes):
    return {
        "client_request_id": str(uuid4()),
        "case_id": "teen-truancy-01",
        "school": "satir",
        "mode": "training",
        "max_rounds": 3,
        **changes,
    }


def message(turn=1, **changes):
    return {
        "client_message_id": str(uuid4()),
        "expected_turn": turn,
        "content": "我想先听听你们每个人的想法。",
        **changes,
    }


def wait_state(client, session_id, *states):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 200, response.text
        data = response.json()
        if data["state"] in states:
            return data
        time.sleep(0.01)
    raise AssertionError(f"Session did not reach {states}: {data}")


def test_create_returns_a_durable_training_resource_before_any_family_reply(tmp_path):
    app = create_app(database_url=str(tmp_path / "training.sqlite3"), training_delay=0.01)
    with TestClient(app) as client:
        response = client.post("/api/v1/sessions", json=command())
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["state"] == "awaiting_input"
        assert data["current_round"] == 0 and data["model_mode"] == "mock"
        assert not [event for event in data["events"] if event["type"] == "utterance"]
        assert client.get(f"/api/v1/reports/{data['session_id']}").json()["status"] == "not_started"


def test_learner_input_gets_family_replies_without_ai_doctor(tmp_path):
    with TestClient(
        create_app(database_url=str(tmp_path / "training.sqlite3"), training_delay=0.01)
    ) as client:
        session_id = client.post("/api/v1/sessions", json=command()).json()["session_id"]
        sent = message()
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=sent).status_code == 202
        data = wait_state(client, session_id, "awaiting_input")
        spoken = [event for event in data["events"] if event["type"] == "utterance"]
        human = [event for event in spoken if event["source"] == "human"]
        assert len(human) == 1 and human[0]["actor"] == "trainee"
        assert human[0]["payload"]["client_message_id"] == sent["client_message_id"]
        assert 1 <= len([event for event in spoken if event["actor"] in {"father", "mother", "patient"}]) <= 3
        assert not [event for event in spoken if event["actor"] == "doctor"]
        assert {event["round"] for event in spoken} == {1}
        assert data["completed_rounds"] == 1


def test_create_idempotency_precedes_active_session_rejection(tmp_path):
    with TestClient(create_app(database_url=str(tmp_path / "training.sqlite3"))) as client:
        request = command()
        first = client.post("/api/v1/sessions", json=request)
        duplicate = client.post("/api/v1/sessions", json=request)
        assert duplicate.status_code == 200
        assert duplicate.json()["session_id"] == first.json()["session_id"]
        conflict = client.post("/api/v1/sessions", json=command())
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["session_id"] == first.json()["session_id"]
        changed = client.post("/api/v1/sessions", json={**request, "school": "bowen"})
        assert changed.status_code == 409
        assert client.get("/api/v1/sessions").json()["total"] == 1


def test_duplicate_message_is_not_run_again_even_after_completion(tmp_path):
    with TestClient(
        create_app(database_url=str(tmp_path / "training.sqlite3"), training_delay=0.01)
    ) as client:
        session_id = client.post("/api/v1/sessions", json=command(max_rounds=1)).json()["session_id"]
        sent = message()
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=sent).status_code == 202
        finished = wait_state(client, session_id, "completed")
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=sent).status_code == 202
        assert client.get(f"/api/v1/sessions/{session_id}").json()["events"] == finished["events"]
        assert (
            client.post(
                f"/api/v1/sessions/{session_id}/messages", json={**sent, "content": "不同内容"}
            ).status_code
            == 409
        )


def test_finish_and_saved_report_survive_a_fresh_application(tmp_path):
    database = str(tmp_path / "training.sqlite3")
    with TestClient(create_app(database_url=database, training_delay=0.01)) as client:
        session_id = client.post("/api/v1/sessions", json=command()).json()["session_id"]
        assert client.post(f"/api/v1/sessions/{session_id}/finish").status_code == 409
        client.post(f"/api/v1/sessions/{session_id}/messages", json=message())
        wait_state(client, session_id, "awaiting_input")
        assert client.post(f"/api/v1/sessions/{session_id}/finish").status_code == 202
        complete = wait_state(client, session_id, "completed")
        report = client.get(f"/api/v1/reports/{session_id}").json()
        assert report["status"] == "ready" and report["kind"] == "workflow_check"
        assert "离线" in report["content"]
        assert complete["report_state"] == "ready"
    with TestClient(create_app(database_url=database)) as client:
        assert client.get(f"/api/v1/sessions/{session_id}").json()["events"] == complete["events"]
        assert client.get(f"/api/v1/reports/{session_id}").json() == report


def test_waiting_session_can_continue_after_restart(tmp_path):
    database = str(tmp_path / "training.sqlite3")
    with TestClient(create_app(database_url=database)) as client:
        session_id = client.post("/api/v1/sessions", json=command(max_rounds=1)).json()["session_id"]
    with TestClient(create_app(database_url=database, training_delay=0.01)) as client:
        assert client.get(f"/api/v1/sessions/{session_id}").json()["state"] == "awaiting_input"
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=message()).status_code == 202
        wait_state(client, session_id, "completed")


def test_cancel_preserves_accepted_input_and_is_idempotent(tmp_path):
    with TestClient(create_app(database_url=str(tmp_path / "training.sqlite3"), training_delay=1)) as client:
        session_id = client.post("/api/v1/sessions", json=command()).json()["session_id"]
        client.post(f"/api/v1/sessions/{session_id}/messages", json=message())
        assert client.post(f"/api/v1/sessions/{session_id}/cancel").status_code == 202
        cancelled = wait_state(client, session_id, "cancelled")
        assert client.post(f"/api/v1/sessions/{session_id}/cancel").status_code == 202
        again = client.get(f"/api/v1/sessions/{session_id}").json()
        assert again["events"] == cancelled["events"]
        assert any(event["source"] == "human" for event in again["events"])
        assert client.get(f"/api/v1/reports/{session_id}").json()["status"] == "cancelled"


def test_generating_session_is_interrupted_after_process_shutdown(tmp_path):
    database = str(tmp_path / "training.sqlite3")
    with TestClient(create_app(database_url=database, training_delay=1)) as client:
        session_id = client.post("/api/v1/sessions", json=command()).json()["session_id"]
        client.post(f"/api/v1/sessions/{session_id}/messages", json=message())
    with TestClient(create_app(database_url=database)) as client:
        data = client.get(f"/api/v1/sessions/{session_id}").json()
        assert data["state"] == "interrupted" and data["completed_rounds"] == 0
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=message(2)).status_code == 409


def test_terminal_sse_replays_persisted_events_and_respects_cursor(tmp_path):
    with TestClient(
        create_app(database_url=str(tmp_path / "training.sqlite3"), training_delay=0.01)
    ) as client:
        session_id = client.post("/api/v1/sessions", json=command(max_rounds=1)).json()["session_id"]
        client.post(f"/api/v1/sessions/{session_id}/messages", json=message())
        snapshot = wait_state(client, session_id, "completed")
        response = client.get(f"/api/v1/sessions/{session_id}/events")
        assert response.status_code == 200
        assert "no-transform" in response.headers["cache-control"]
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert events == snapshot["events"]
        assert [event["seq"] for event in events] == sorted({event["seq"] for event in events})
        cursor = snapshot["last_event_seq"]
        assert client.get(f"/api/v1/sessions/{session_id}/events?after={cursor}").text == ""
        assert (
            client.get(f"/api/v1/sessions/{session_id}/events", headers={"Last-Event-ID": str(cursor)}).text
            == ""
        )
        assert client.get(f"/api/v1/sessions/{session_id}/events?after={cursor + 1}").status_code == 409


@pytest.mark.parametrize("changes", [{"school": "other"}, {"max_rounds": 31}, {"case_id": "missing-case"}])
def test_bad_create_never_persists_a_training_session(tmp_path, changes):
    with TestClient(create_app(database_url=str(tmp_path / "training.sqlite3"))) as client:
        response = client.post("/api/v1/sessions", json=command(**changes))
        assert response.status_code in {404, 422}
        assert client.get("/api/v1/sessions").json()["total"] == 0


def test_unknown_database_version_keeps_diagnostics_available_and_shuts_down_cleanly(tmp_path):
    database = tmp_path / "future.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")
    with TestClient(create_app(database_url=str(database))) as client:
        assert client.get("/healthz").status_code == 200
        readiness = client.get("/readyz")
        assert readiness.status_code == 503
        assert str(tmp_path) not in readiness.text
        assert client.post("/api/v1/sessions", json=command()).status_code == 503


def test_retired_demo_cannot_bypass_the_single_active_session_rule(tmp_path, monkeypatch):
    from app.routers import sessions

    async def forbidden_runner(**kwargs):
        raise AssertionError("retired demo must not run")

    monkeypatch.setattr(sessions, "run_session", forbidden_runner)
    with TestClient(create_app(database_url=str(tmp_path / "training.sqlite3"))) as client:
        session_id = client.post("/api/v1/sessions", json=command()).json()["session_id"]
        assert client.post("/api/v1/demo/sessions", json={}).status_code == 410
        assert client.get(f"/api/v1/sessions/{session_id}").json()["state"] == "awaiting_input"
