"""Offline delivery workflow: practice, review, backup and exact restore."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from app.main import create_app
from fastapi.testclient import TestClient
from healteam_memory.backup import BackupError, create_backup, restore_backup, verify_backup

from tests.api.test_evaluation_api import submission
from tests.api.test_live_api import command, message, wait_for


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith(("HEALTEAM_", "LLM_", "ANTHROPIC_", "OPENAI_", "DEEPSEEK_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HEALTEAM_MODEL_MODE", "mock")


def test_backed_up_training_review_and_idempotent_receipts_survive_restore(tmp_path):
    database = tmp_path / "source.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    archive = tmp_path / "training.healteam-backup.zip"
    with TestClient(create_app(database_url=str(database), training_delay=0.001)) as client:
        created = client.post("/api/v1/sessions", json=command()).json()
        session_id = created["session_id"]
        learner_message = message()
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=learner_message).status_code == 202
        before = wait_for(client, session_id, "completed")
        report = client.get(f"/api/v1/reports/{session_id}").json()
        review = submission(client, session_id, report)
        assert client.post(f"/api/v1/reports/{session_id}/reviews", json=review).status_code == 201
        review_history = client.get(f"/api/v1/reports/{session_id}/reviews").json()
        with pytest.raises(BackupError, match="runtime_in_use"):
            create_backup(database, archive)
    create_backup(database, archive)
    assert verify_backup(archive)["schema_version"] == 2
    restore_backup(archive, restored)
    with TestClient(create_app(database_url=str(restored), training_delay=0.001)) as client:
        assert client.get(f"/api/v1/sessions/{session_id}").json() == before
        assert client.get(f"/api/v1/reports/{session_id}").json() == report
        assert client.get(f"/api/v1/reports/{session_id}/reviews").json() == review_history
        assert client.post(f"/api/v1/sessions/{session_id}/messages", json=learner_message).status_code == 202
        assert client.post(f"/api/v1/reports/{session_id}/reviews", json=review).status_code == 200
        assert client.get(f"/api/v1/sessions/{session_id}").json() == before
        assert client.get(f"/api/v1/reports/{session_id}/reviews").json() == review_history
        assert (
            client.post("/api/v1/sessions", json=command(client_request_id=str(uuid4()))).status_code == 201
        )


def test_source_packaging_excludes_local_runtime_and_sensitive_outputs():
    from scripts.package_source import PRUNE, SOURCE, allowed

    assert {".local", "backups", ".cache", ".venv", "node_modules"} <= PRUNE
    assert not allowed(SOURCE / "healteam" / ".env")
    assert not allowed(SOURCE / "healteam" / "training.healteam-backup.zip")
    assert not allowed(SOURCE / "healteam" / "training.sqlite3")
    assert allowed(SOURCE / "healteam" / "data" / "rubrics" / "trial-family-training-v1.json")


def test_source_archive_cannot_be_written_inside_its_own_input(monkeypatch, tmp_path):
    from scripts import package_source

    monkeypatch.setattr(package_source, "SOURCE", tmp_path)
    monkeypatch.setattr("sys.argv", ["package_source.py", "--output", str(tmp_path / "nested.zip")])
    with pytest.raises(SystemExit) as error:
        package_source.main()
    assert error.value.code == 2
    assert not (tmp_path / "nested.zip").exists()
