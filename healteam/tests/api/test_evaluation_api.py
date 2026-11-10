"""Real local model fixture, immutable AI report and append-only human review."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

from tests.api.test_live_api import command, message, wait_for
from tests.providers.fake_server import fake_provider


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith(("HEALTEAM_", "LLM_", "ANTHROPIC_", "OPENAI_", "DEEPSEEK_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HEALTEAM_DATABASE_URL", str(tmp_path / "evaluation.sqlite3"))
    monkeypatch.setenv("HEALTEAM_MODEL_MODE", "live")
    monkeypatch.setenv("HEALTEAM_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HEALTEAM_LLM_API_KEY", "local-fixture-key-s4")
    monkeypatch.setenv("HEALTEAM_LLM_MODEL", "healteam-local-fixture")


def complete(client, *, mode="training"):
    session_id = client.post("/api/v1/sessions", json=command(mode=mode)).json()["session_id"]
    if mode == "training":
        client.post(f"/api/v1/sessions/{session_id}/messages", json=message())
    wait_for(client, session_id, "completed")
    return session_id, client.get(f"/api/v1/reports/{session_id}").json()


def submission(client, session_id, report, *, expected_revision=0):
    binding = client.get(f"/api/v1/reports/{session_id}/reviews").json()
    return {
        "client_review_id": str(uuid4()),
        "expected_revision": expected_revision,
        "report_hash": binding["report_hash"],
        "rubric_hash": binding["rubric_hash"],
        "reviewer_name": "本地测试复核者",
        "notes": "私有复核备注",
        "dimensions": [
            {
                "dimension_id": item["dimension_id"],
                "rating": None,
                "rationale": "该片段尚不足以判断",
                "evidence_seq": [],
            }
            for item in report["rubric"]["dimensions"]
        ],
        "milestones": [
            {
                "milestone_id": item["milestone_id"],
                "status": "insufficient_evidence",
                "rationale": "不把预设目标当作已观察事实",
                "evidence_seq": [],
            }
            for item in report["rubric"]["milestones"]
        ],
    }


def test_review_revisions_are_durable_idempotent_and_do_not_rewrite_report(monkeypatch):
    with fake_provider() as (fixture, base):
        monkeypatch.setenv("HEALTEAM_LLM_BASE_URL", base)
        with TestClient(create_app(training_delay=0.001)) as client:
            session_id, report = complete(client)
            before = client.get(f"/api/v1/sessions/{session_id}").json()
            assert report["rubric"]["status"] == "trial_unvalidated"
            assert len(report["assessment"]["dimensions"]) == 4
            request = submission(client, session_id, report)
            path = f"/api/v1/reports/{session_id}/reviews"
            first = client.post(path, json=request)
            assert first.status_code == 201, first.text
            assert first.json()["revision"] == 1 and first.json()["reviewer_identity_verified"] is False
            assert client.post(path, json=request).status_code == 200
            stale = client.post(path, json={**request, "client_review_id": str(uuid4())})
            assert stale.status_code == 409 and stale.json()["detail"]["current_revision"] == 1
            revised = {
                **request,
                "client_review_id": str(uuid4()),
                "expected_revision": 1,
                "notes": "追加修订",
            }
            assert client.post(path, json=revised).status_code == 201
            history = client.get(path).json()
            assert history["latest_revision"] == 2 and [item["revision"] for item in history["items"]] == [
                1,
                2,
            ]
            assert client.get(f"/api/v1/reports/{session_id}").json() == report
            assert client.get(f"/api/v1/sessions/{session_id}").json() == before
            assert fixture.calls == 2
        with TestClient(create_app()) as client:
            assert client.get(path).json() == history
            result = client.get("/api/v1/calibration").json()
            assert result["reviewed_sessions"] == 1 and result["paired_dimensions"] == 0
            assert result["insufficient_pairs"] == 4 and result["exact_agreement"] is None


@pytest.mark.parametrize(
    "change,expected",
    [({"report_hash": "0" * 64}, 409), ({"rubric_hash": "0" * 64}, 409), ({"expected_revision": True}, 422)],
)
def test_invalid_review_binding_or_revision_is_rejected(monkeypatch, change, expected):
    with fake_provider() as (_fixture, base):
        monkeypatch.setenv("HEALTEAM_LLM_BASE_URL", base)
        with TestClient(create_app(training_delay=0.001)) as client:
            session_id, report = complete(client)
            payload = {**submission(client, session_id, report), **change}
            path = f"/api/v1/reports/{session_id}/reviews"
            assert client.post(path, json=payload).status_code == expected
            assert client.get(path).json()["latest_revision"] == 0


def test_evidence_must_be_public_and_include_the_learner_for_a_rating(monkeypatch):
    with fake_provider() as (_fixture, base):
        monkeypatch.setenv("HEALTEAM_LLM_BASE_URL", base)
        with TestClient(create_app(training_delay=0.001)) as client:
            session_id, report = complete(client)
            payload = submission(client, session_id, report)
            snapshot = client.get(f"/api/v1/sessions/{session_id}").json()
            family = next(
                event["seq"]
                for event in snapshot["events"]
                if event["type"] == "utterance" and event["actor"] == "father"
            )
            path = f"/api/v1/reports/{session_id}/reviews"
            for evidence in ([family], [999999]):
                payload["dimensions"][0].update(rating=1, evidence_seq=evidence)
                assert client.post(path, json=payload).status_code == 422
            payload["dimensions"][0].update(rating=True, evidence_seq=[family])
            assert client.post(path, json=payload).status_code == 422


@pytest.mark.parametrize("mode,model_mode", [("training", "mock"), ("demo", "live")])
def test_mock_and_demo_reviews_do_not_enter_learner_ai_agreement(monkeypatch, mode, model_mode):
    with fake_provider() as (_fixture, base):
        monkeypatch.setenv("HEALTEAM_LLM_BASE_URL", base)
        monkeypatch.setenv("HEALTEAM_MODEL_MODE", model_mode)
        with TestClient(create_app(training_delay=0.001)) as client:
            session_id, report = complete(client, mode=mode)
            if model_mode == "mock":
                assert report["assessment"] is None
            assert (
                client.post(
                    f"/api/v1/reports/{session_id}/reviews", json=submission(client, session_id, report)
                ).status_code
                == 201
            )
            summary = client.get("/api/v1/calibration").json()
            assert summary["eligible_sessions"] == summary["paired_dimensions"] == 0
            assert summary["exact_agreement"] is None


def test_export_contains_numeric_annotations_without_names_or_dialogue(monkeypatch):
    with fake_provider() as (_fixture, base):
        monkeypatch.setenv("HEALTEAM_LLM_BASE_URL", base)
        with TestClient(create_app(training_delay=0.001)) as client:
            session_id, report = complete(client)
            client.post(f"/api/v1/reports/{session_id}/reviews", json=submission(client, session_id, report))
            response = client.get("/api/v1/calibration/export")
            assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
            exported = response.json()
            assert len(exported["samples"]) == 1
            assert all(
                value not in response.text
                for value in (
                    "本地测试复核者",
                    "私有复核备注",
                    session_id,
                    "请爸爸",
                    "local-fixture-key-s4",
                    base,
                )
            )
            assert exported["contains_transcript"] is False
            assert (
                json.dumps(report["assessment"]["reviewed_event_seq"]) in response.text
                or exported["samples"][0]["reviewed_event_seq"] == report["assessment"]["reviewed_event_seq"]
            )
