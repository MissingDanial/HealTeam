from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from app.services.calibration import calibration_export, calibration_summary
from healteam_memory.evaluation_contracts import DimensionId
from healteam_memory.review_store import report_fingerprint

REFERENCE = json.loads(
    (Path(__file__).parents[1] / "fixtures/evaluation-reference-cases.json").read_text(encoding="utf-8")
)


def sample(ai_values=None, human_values=None, *, model="fixture", mode="training", model_mode="live"):
    ai_values = [1, 1, 1, 1] if ai_values is None else ai_values
    human_values = [1, 1, 1, 1] if human_values is None else human_values
    session_id = str(uuid4())
    rubric = {"rubric_version": "1.0.0", "school": "satir", "status": "trial_unvalidated"}
    rubric["sha256"] = hashlib.sha256(
        json.dumps(rubric, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()

    def dimensions(values):
        return [
            {
                "dimension_id": item.value,
                "rating": value,
                "rationale": "private-rationale",
                "evidence_seq": [1] if value is not None else [],
            }
            for item, value in zip(DimensionId, values, strict=True)
        ]

    report = {
        "kind": "ai_review",
        "session_id": session_id,
        "rubric": rubric,
        "assessment": {
            "rubric_version": rubric["rubric_version"],
            "rubric_hash": rubric["sha256"],
            "dimensions": dimensions(ai_values),
            "milestones": [],
            "reviewed_event_seq": [1, 2],
            "total_utterances": 3,
        },
    }
    human = {
        "review_id": str(uuid4()),
        "session_id": session_id,
        "client_review_id": str(uuid4()),
        "revision": 2,
        "created_at": "2026-11-04T00:00:00Z",
        "report_hash": report_fingerprint(report),
        "rubric_hash": rubric["sha256"],
        "reviewer_name": "private-reviewer",
        "notes": "private-notes",
        "dimensions": dimensions(human_values),
        "milestones": [],
    }
    return {
        "session_id": session_id,
        "school": "satir",
        "mode": mode,
        "model_mode": model_mode,
        "provider": "openai",
        "model": model,
        "report": report,
        "latest_review": human,
    }


@pytest.mark.parametrize("reference", REFERENCE["cases"], ids=lambda item: item["name"])
def test_synthetic_reference_agreement_denominators(reference):
    assert REFERENCE["provenance"] == "synthetic_software_reference_not_expert_annotations"
    result = calibration_summary([sample(reference["ai"], reference["human"])])
    assert result["status"] == "awaiting_expert_calibration"
    assert result["paired_dimensions"] == reference["pairs"]
    assert result["exact_matches"] == reference["matches"]
    assert result["insufficient_pairs"] == reference["insufficient"]
    assert result["exact_agreement"] == (
        reference["matches"] / reference["pairs"] if reference["pairs"] else None
    )


def test_empty_and_unreviewed_samples_never_show_a_percentage():
    assert calibration_summary([])["exact_agreement"] is None
    item = sample()
    item["latest_review"] = None
    result = calibration_summary([item])
    assert result["eligible_sessions"] == 1 and result["reviewed_sessions"] == 0
    assert result["paired_dimensions"] == result["insufficient_pairs"] == 0
    assert result["groups"][0]["exact_agreement"] is None


def test_demo_mock_and_legacy_do_not_become_learner_calibration_pairs():
    legacy = sample()
    legacy["report"].pop("assessment")
    result = calibration_summary([sample(), sample(mode="demo"), sample(model_mode="mock"), legacy])
    assert result["total_sessions"] == 4 and result["eligible_sessions"] == 1
    assert result["reviewed_sessions"] == 1 and result["excluded_demo_sessions"] == 1
    assert result["paired_dimensions"] == 4


def test_distinct_models_have_distinct_groups():
    result = calibration_summary([sample(model="one"), sample(model="two", human_values=[0] * 4)])
    assert len(result["groups"]) == 2
    assert result["paired_dimensions"] == 8 and result["exact_agreement"] == 0.5


def test_stale_binding_is_not_reused_for_an_updated_report():
    item = sample()
    item["latest_review"]["report_hash"] = "0" * 64
    result = calibration_summary([item])
    assert result["eligible_sessions"] == 1 and result["reviewed_sessions"] == 0
    assert calibration_export([item])["samples"] == []


def test_corrupted_rubric_body_does_not_enter_statistics():
    item = sample()
    item["report"]["rubric"]["school"] = "bowen"
    assert calibration_summary([item])["eligible_sessions"] == 0


def test_human_evidence_outside_ai_window_is_reported_and_excluded():
    item = sample()
    item["latest_review"]["dimensions"][0]["evidence_seq"] = [3]
    result = calibration_summary([item])
    assert result["paired_dimensions"] == result["exact_matches"] == 3
    assert result["out_of_window_pairs"] == 1
    assert result["insufficient_pairs"] == 0
    assert result["groups"][0]["out_of_window_pairs"] == 1


def test_export_omits_text_names_notes_and_source_identifiers():
    item = sample()
    before = copy.deepcopy(item)
    result = calibration_export([item])
    serialized = json.dumps(result)
    assert all(
        value not in serialized
        for value in ("private-rationale", "private-reviewer", "private-notes", item["session_id"])
    )
    assert (
        result["contains_transcript"]
        is result["contains_notes"]
        is result["contains_reviewer_names"]
        is False
    )
    assert result["samples"][0]["review_revision"] == 2
    assert result["samples"][0]["reviewed_event_seq"] == [1, 2]
    assert item == before


def test_invalid_human_record_does_not_remove_a_valid_ai_sample():
    item = sample()
    item["latest_review"] = {}
    result = calibration_summary([item])
    assert result["eligible_sessions"] == 1 and result["reviewed_sessions"] == 0
    assert result["exact_agreement"] is None


def test_json_null_report_cannot_break_other_samples_or_export():
    invalid = sample()
    invalid["report"] = None
    valid = sample()
    result = calibration_summary([invalid, valid])
    assert result["total_sessions"] == 2 and result["eligible_sessions"] == 1
    assert len(calibration_export([invalid, valid])["samples"]) == 1
