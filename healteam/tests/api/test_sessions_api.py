from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.main import create_app  # noqa: E402
from app.routers import sessions as sessions_router  # noqa: E402


@pytest.fixture(autouse=True)
def offline_temporary_database(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTEAM_MODEL_MODE", "mock")
    monkeypatch.setenv("HEALTEAM_DATABASE_URL", str(tmp_path / "legacy.sqlite3"))


def _collect_sse_events(response: TestClient) -> list[str]:
    events: list[str] = []
    for line in response.iter_lines():
        if line.startswith("event: "):
            events.append(line.removeprefix("event: ").strip())
    return events


def test_create_session_streams_events_and_persists_history(monkeypatch) -> None:
    async def fake_run_session(*, case_id: str, school_str: str, max_rounds: int, queue):
        await queue.put({"type": "start", "case_id": case_id})
        await queue.put({"type": "session_end", "rounds": max_rounds})
        await queue.put({"type": "complete"})
        return {"session_id": "session-123", "rounds": max_rounds}

    monkeypatch.setattr(sessions_router, "run_session", fake_run_session)
    sessions_router._session_history.clear()
    app = create_app(allow_legacy_demo=True)

    with TestClient(app) as client:
        with client.stream("POST", "/api/v1/demo/sessions", json={"max_rounds": 3}) as response:
            events = _collect_sse_events(response)

        history = client.get("/api/v1/demo/sessions").json()

    assert response.status_code == 200
    assert events == ["start", "session_end", "complete"]
    assert history == [
        {
            "case_id": "teen-truancy-01",
            "school": "structural",
            "max_rounds": 3,
            "session_id": "session-123",
            "rounds": 3,
        }
    ]


def test_create_session_returns_error_immediately_when_runner_crashes(monkeypatch) -> None:
    async def crashing_run_session(*, case_id: str, school_str: str, max_rounds: int, queue):
        raise RuntimeError("runner crashed")

    monkeypatch.setattr(sessions_router, "run_session", crashing_run_session)
    sessions_router._session_history.clear()
    app = create_app(allow_legacy_demo=True)

    with TestClient(app) as client:
        with client.stream("POST", "/api/v1/demo/sessions", json={}) as response:
            events = _collect_sse_events(response)

        history = client.get("/api/v1/demo/sessions").json()

    assert response.status_code == 200
    assert events == ["error"]
    assert history == []
