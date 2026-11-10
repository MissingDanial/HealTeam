"""Offline S0 smoke: imports, content, HTTP preflight and 20-round graph."""

from __future__ import annotations

import os
import random
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


def main() -> None:
    # This command is intentionally offline, regardless of inherited keys.
    os.environ["HEALTEAM_MODEL_MODE"] = "mock"
    from app.main import create_app
    from fastapi.testclient import TestClient
    from healteam_agents import Orchestrator
    from healteam_agents.case_loader import build_case_context
    from healteam_memory import EventType, School, Session

    for school in School:
        context = build_case_context("teen-truancy-01", school)
        assert context["opening_context"]["scene"]
        assert context["milestones"][0]["validation_signals"]
    random.seed(1)
    session = Session(case_id="teen-truancy-01", school=School.STRUCTURAL)
    Orchestrator(max_rounds=20).run(session)
    assert session.round == 20
    assert {event.round for event in session.events if event.type == EventType.UTTERANCE} == set(range(1, 21))
    with TemporaryDirectory(prefix="healteam-smoke-") as temporary:
        database = str(Path(temporary) / "training.sqlite3")
        with TestClient(create_app(database_url=database, training_delay=0.001)) as client:
            assert client.get("/healthz").status_code == 200
            assert client.get("/readyz").status_code == 200
            cases = client.get("/api/v1/cases")
            assert cases.status_code == 200 and cases.json()
            created = client.post(
                "/api/v1/sessions",
                json={
                    "client_request_id": str(uuid4()),
                    "case_id": "teen-truancy-01",
                    "school": "satir",
                    "mode": "training",
                    "max_rounds": 1,
                },
            )
            assert created.status_code == 201
            identifier = created.json()["session_id"]
            accepted = client.post(
                f"/api/v1/sessions/{identifier}/messages",
                json={
                    "client_message_id": str(uuid4()),
                    "expected_turn": 1,
                    "content": "请大家分别说说自己的想法。",
                },
            )
            assert accepted.status_code == 202
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                snapshot = client.get(f"/api/v1/sessions/{identifier}").json()
                if snapshot["state"] == "completed":
                    break
                time.sleep(0.01)
            assert snapshot["state"] == "completed"
            assert not any(event["actor"] == "doctor" for event in snapshot["events"])
        with TestClient(create_app(database_url=database)) as client:
            assert client.get(f"/api/v1/reports/{identifier}").json()["status"] == "ready"
    print("PASS: content, three schools, 20 numbered demo rounds, learner turn, durable report reopen")
    print("Offline mock check only; no external model was called.")


if __name__ == "__main__":
    main()
