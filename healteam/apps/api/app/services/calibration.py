"""Descriptive local annotation agreement; never a clinical validation claim."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from healteam_memory.evaluation_contracts import Assessment, CalibrationSummary, ReviewRecord
from healteam_memory.review_store import report_fingerprint
from pydantic import ValidationError


def _eligible(sample: dict[str, Any]) -> bool:
    report = sample.get("report")
    return (
        isinstance(report, dict)
        and sample["mode"] == "training"
        and sample["model_mode"] == "live"
        and report.get("kind") == "ai_review"
        and isinstance(report.get("rubric"), dict)
        and isinstance(report.get("assessment"), dict)
    )


def _pair(sample: dict[str, Any]) -> tuple[Assessment, ReviewRecord | None] | None:
    if not _eligible(sample):
        return None
    try:
        rubric = sample["report"]["rubric"]
        digest = sha256(
            json.dumps(
                {key: value for key, value in rubric.items() if key != "sha256"},
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        ai = Assessment.model_validate(sample["report"]["assessment"])
        if (
            ai.rubric_hash != rubric["sha256"]
            or ai.rubric_version != rubric["rubric_version"]
            or digest != ai.rubric_hash
        ):
            return None
    except (ValidationError, ValueError, KeyError, TypeError):
        return None
    try:
        review = sample.get("latest_review")
        if review is None:
            return ai, None
        human = ReviewRecord.model_validate(review)
        if (
            human.report_hash != report_fingerprint(sample["report"])
            or human.rubric_hash != ai.rubric_hash
            or str(human.session_id) != sample["session_id"]
        ):
            return ai, None
        return ai, human
    except (ValidationError, ValueError, KeyError, TypeError):
        return ai, None


def calibration_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple, dict[str, Any]] = {}
    eligible = reviewed = pairs = matches = insufficient = out_of_window = 0
    for sample in samples:
        pair = _pair(sample)
        if pair is None:
            continue
        eligible += 1
        ai, human = pair
        key = (
            ai.rubric_hash,
            ai.rubric_version,
            sample["school"],
            sample.get("provider"),
            sample.get("model"),
        )
        group = groups.setdefault(
            key,
            {
                "rubric_hash": key[0],
                "rubric_version": key[1],
                "school": key[2],
                "provider": key[3],
                "model": key[4],
                "reviewed_sessions": 0,
                "paired_dimensions": 0,
                "exact_matches": 0,
                "insufficient_pairs": 0,
                "out_of_window_pairs": 0,
                "exact_agreement": None,
            },
        )
        if human is None:
            continue
        reviewed += 1
        group["reviewed_sessions"] += 1
        human_values = {item.dimension_id: item for item in human.dimensions}
        for item in ai.dimensions:
            actual = human_values[item.dimension_id]
            if item.rating is None or actual.rating is None:
                insufficient += 1
                group["insufficient_pairs"] += 1
            elif not set(actual.evidence_seq) <= set(ai.reviewed_event_seq):
                out_of_window += 1
                group["out_of_window_pairs"] += 1
            else:
                pairs += 1
                group["paired_dimensions"] += 1
                if item.rating == actual.rating:
                    matches += 1
                    group["exact_matches"] += 1
    for group in groups.values():
        count = group["paired_dimensions"]
        group["exact_agreement"] = group["exact_matches"] / count if count else None
    return CalibrationSummary(
        total_sessions=len(samples),
        eligible_sessions=eligible,
        reviewed_sessions=reviewed,
        paired_dimensions=pairs,
        exact_matches=matches,
        exact_agreement=matches / pairs if pairs else None,
        insufficient_pairs=insufficient,
        out_of_window_pairs=out_of_window,
        excluded_demo_sessions=sum(sample["mode"] == "demo" for sample in samples),
        groups=sorted(
            groups.values(),
            key=lambda group: (
                group["rubric_hash"],
                group["school"],
                group["provider"] or "",
                group["model"] or "",
            ),
        ),
    ).model_dump(mode="json")


def calibration_export(samples: list[dict[str, Any]]) -> dict[str, Any]:
    exported = []
    for sample in samples:
        pair = _pair(sample)
        if pair is None or pair[1] is None:
            continue
        ai, human = pair
        sample_id = sha256((sample["session_id"] + human.report_hash).encode("utf-8")).hexdigest()

        def dimensions(items):
            return [
                {
                    "dimension_id": item.dimension_id.value,
                    "rating": item.rating,
                    "evidence_seq": item.evidence_seq,
                }
                for item in items
            ]

        def milestones(items):
            return [
                {"milestone_id": item.milestone_id, "status": item.status, "evidence_seq": item.evidence_seq}
                for item in items
            ]

        exported.append(
            {
                "sample_id": sample_id,
                "school": sample["school"],
                "provider": sample.get("provider"),
                "model": sample.get("model"),
                "rubric_version": ai.rubric_version,
                "rubric_hash": ai.rubric_hash,
                "review_revision": human.revision,
                "ai_dimensions": dimensions(ai.dimensions),
                "reviewed_event_seq": ai.reviewed_event_seq,
                "total_utterances": ai.total_utterances,
                "human_dimensions": dimensions(human.dimensions),
                "ai_milestones": milestones(ai.milestones),
                "human_milestones": milestones(human.milestones),
            }
        )
    return {
        "format": "healteam-calibration-v1",
        "status": "unvalidated_local_annotations",
        "generated_at": datetime.now(UTC).isoformat(),
        "contains_transcript": False,
        "contains_reviewer_names": False,
        "contains_notes": False,
        "summary": calibration_summary(samples),
        "samples": exported,
    }


__all__ = ["calibration_summary", "calibration_export"]
