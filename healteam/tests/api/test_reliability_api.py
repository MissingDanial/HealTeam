"""S2 API boundaries with a real file database and controlled failures."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from uuid import uuid4

import pytest
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTEAM_MODEL_MODE", "mock")
    monkeypatch.setenv("HEALTEAM_DATABASE_URL", str(tmp_path / "training.sqlite3"))
    monkeypatch.setenv("HEALTEAM_OPERATION_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("HEALTEAM_STOP_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setenv("HEALTEAM_STORAGE_BUSY_TIMEOUT_SECONDS", "0.05")


def create_command(**changes):
    return {
        "client_request_id": str(uuid4()),
        "case_id": "teen-truancy-01",
        "school": "satir",
        "mode": "training",
        "max_rounds": 1,
        **changes,
    }


def message():
    return {"client_message_id": str(uuid4()), "expected_turn": 1, "content": "请爸爸先说说。"}


def wait_for(client, session_id, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 200, response.text
        view = response.json()
        if predicate(view):
            return view
        time.sleep(0.01)
    raise AssertionError(f"Session did not reach expected condition: {view}")


def test_second_runtime_is_unready_and_cannot_interrupt_the_owner(tmp_path):
    with TestClient(create_app(training_delay=0.2)) as first:
        session_id = first.post("/api/v1/sessions", json=create_command()).json()["session_id"]
        first.post(f"/api/v1/sessions/{session_id}/messages", json=message())
        with TestClient(create_app()) as second:
            assert second.get("/healthz").status_code == 200
            ready = second.get("/readyz")
            assert ready.status_code == 503
            assert ready.json()["issues"] == ["runtime_in_use"]
            blocked = second.get(f"/api/v1/sessions/{session_id}")
            assert blocked.status_code == 503
            assert blocked.json() == {"detail": {"code": "runtime_in_use"}}
            assert str(tmp_path) not in blocked.text + ready.text
            assert first.get(f"/api/v1/sessions/{session_id}").json()["state"] != "interrupted"
        result = wait_for(first, session_id, lambda view: view["state"] == "completed")
        assert not any(event["payload"].get("reason") == "process_interrupted" for event in result["events"])


def test_failed_startup_releases_runtime_ownership(tmp_path):
    database = tmp_path / "training.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 999")
    with TestClient(create_app()) as client:
        assert client.get("/readyz").status_code == 503
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 0")
    with TestClient(create_app()) as client:
        assert client.get("/readyz").status_code == 200


@pytest.mark.parametrize(
    "variable",
    [
        "HEALTEAM_OPERATION_TIMEOUT_SECONDS",
        "HEALTEAM_STOP_TIMEOUT_SECONDS",
        "HEALTEAM_STORAGE_BUSY_TIMEOUT_SECONDS",
    ],
)
def test_invalid_reliability_budget_is_safe_and_unready(monkeypatch, variable):
    monkeypatch.setenv(variable, "0")
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["checks"]["configuration"] == "invalid"
        assert client.post("/api/v1/sessions", json=create_command()).status_code == 503


def test_response_timeout_saves_the_accepted_input_without_replaying_it(monkeypatch):
    monkeypatch.setenv("HEALTEAM_OPERATION_TIMEOUT_SECONDS", "0.02")
    with TestClient(create_app(training_delay=0.2)) as client:
        session_id = client.post("/api/v1/sessions", json=create_command()).json()["session_id"]
        command = message()
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=command).status_code == 202
        failed = wait_for(client, session_id, lambda view: view["state"] == "failed")
        assert failed["error_code"] == "response_timeout" and failed["completed_rounds"] == 0
        assert len([event for event in failed["events"] if event["source"] == "human"]) == 1
        assert not [
            event for event in failed["events"] if event["type"] == "utterance" and event["source"] == "ai"
        ]
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=command).status_code == 202
        assert client.get(f"/api/v1/sessions/{session_id}").json()["events"] == failed["events"]
        assert client.post("/api/v1/sessions", json=create_command()).status_code == 201


def test_database_busy_returns_safe_error_then_same_message_commits_once(tmp_path):
    with TestClient(create_app(training_delay=0.005)) as client:
        session_id = client.post("/api/v1/sessions", json=create_command()).json()["session_id"]
        command = message()
        with sqlite3.connect(tmp_path / "training.sqlite3") as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            response = client.post(f"/api/v1/sessions/{session_id}/messages", json=command)
            assert time.monotonic() - started < 2
            assert response.status_code == 503
            assert response.json() == {"detail": {"code": "storage_unavailable"}}
            blocker.rollback()
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=command).status_code == 202
        complete = wait_for(client, session_id, lambda view: view["state"] == "completed")
        assert len([event for event in complete["events"] if event["source"] == "human"]) == 1


def test_interrupted_report_retries_saved_evidence_without_replaying_dialogue():
    app = create_app(training_delay=0.005)
    with TestClient(app) as client:

        async def blocked_report(record):
            await asyncio.sleep(60)

        app.state.training_manager._generate_report = blocked_report
        session_id = client.post("/api/v1/sessions", json=create_command()).json()["session_id"]
        client.post(f"/api/v1/sessions/{session_id}/messages", json=message())
        before = wait_for(client, session_id, lambda view: view["phase"] == "report")
    with TestClient(create_app(training_delay=0.005)) as client:
        interrupted = client.get(f"/api/v1/sessions/{session_id}").json()
        assert interrupted["state"] == "interrupted" and interrupted["report_state"] == "failed"
        assert client.post(f"/api/v1/reports/{session_id}/retry").status_code == 202
        complete = wait_for(client, session_id, lambda view: view["state"] == "completed")
        assert complete["error_code"] is None

        def spoken(view):
            return [event for event in view["events"] if event["type"] == "utterance"]

        assert spoken(before) == spoken(complete)
        assert len([event for event in complete["events"] if event["type"] == "report_ready"]) == 1
