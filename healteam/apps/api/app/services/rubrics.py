"""Versioned project-authored teaching observations, never a clinical scale."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..resources import CaseResourceError, project_root

DIMENSION_IDS = ("participation", "exploration", "school_consistency", "collaborative_next_step")
RUBRIC_FILE = "trial-family-training-v1.json"


class _RubricModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Dimension(_RubricModel):
    dimension_id: Literal["participation", "exploration", "school_consistency", "collaborative_next_step"]
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    anchors: dict[str, str]


class _Resource(_RubricModel):
    rubric_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    status: Literal["trial_unvalidated"]
    notice: str = Field(min_length=1, max_length=1000)
    dimensions: list[_Dimension] = Field(min_length=4, max_length=4)
    missing_evidence_rule: str = Field(min_length=1, max_length=1000)
    milestone_rule: str = Field(min_length=1, max_length=1000)
    case_milestone_actors: dict[str, dict[str, list[Literal["father", "mother", "patient"]]]]


class _Milestone(_RubricModel):
    milestone_id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=1000)
    description: str = Field(min_length=1, max_length=2000)
    validation_signals: list[str] = Field(min_length=1, max_length=20)
    required_actors: list[Literal["father", "mother", "patient"]] = Field(min_length=1, max_length=3)


class _Snapshot(_RubricModel):
    rubric_id: str
    rubric_version: str
    status: Literal["trial_unvalidated"]
    notice: str
    school: Literal["structural", "bowen", "satir"]
    dimensions: list[_Dimension] = Field(min_length=4, max_length=4)
    milestones: list[_Milestone] = Field(max_length=30)
    missing_evidence_rule: str
    milestone_rule: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def validate_rubric_snapshot(data: Any, *, school: str) -> dict[str, Any]:
    try:
        parsed = _Snapshot.model_validate(data)
        if parsed.school != school or {item.dimension_id for item in parsed.dimensions} != set(DIMENSION_IDS):
            raise ValueError
        if any(
            set(item.anchors) != {"0", "1", "2"} or not all(value.strip() for value in item.anchors.values())
            for item in parsed.dimensions
        ):
            raise ValueError
        if len({item.milestone_id for item in parsed.milestones}) != len(parsed.milestones):
            raise ValueError
        body = {key: value for key, value in data.items() if key != "sha256"}
        digest = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if digest != parsed.sha256:
            raise ValueError
    except (ValidationError, KeyError, TypeError, AttributeError):
        raise ValueError("Invalid frozen evaluation rubric") from None
    return data


def snapshot_rubric(school: str, case: dict[str, Any]) -> dict[str, Any]:
    """Freeze the trial resource and applicable case goals without new claims."""
    if school not in {"structural", "bowen", "satir"}:
        raise CaseResourceError("invalid_school")
    try:
        resource = _Resource.model_validate_json(
            (project_root() / "data" / "rubrics" / RUBRIC_FILE).read_text(encoding="utf-8")
        )
        dimensions = [item.model_dump() for item in resource.dimensions]
        if {item["dimension_id"] for item in dimensions} != set(DIMENSION_IDS):
            raise ValueError
        if any(
            set(item["anchors"]) != {"0", "1", "2"}
            or not all(value.strip() for value in item["anchors"].values())
            for item in dimensions
        ):
            raise ValueError
        actor_map = resource.case_milestone_actors.get(case["case_id"], {})
        milestones = [
            {
                "milestone_id": item["id"],
                "label": item["label"],
                "description": item["description"],
                "validation_signals": list(item["validation_signals"]),
                "required_actors": actor_map.get(item["id"], []),
            }
            for item in case["expected_milestones"]
            if school in item["primary_school"]
        ]
        data = {
            "rubric_id": resource.rubric_id,
            "rubric_version": resource.version,
            "status": resource.status,
            "notice": resource.notice,
            "school": school,
            "dimensions": dimensions,
            "milestones": milestones,
            "missing_evidence_rule": resource.missing_evidence_rule,
            "milestone_rule": resource.milestone_rule,
        }
    except (OSError, UnicodeError, ValidationError, ValueError, KeyError, TypeError):
        raise CaseResourceError("rubric_resource_unavailable", 503) from None
    data["sha256"] = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    try:
        validate_rubric_snapshot(data, school=school)
    except ValueError:
        raise CaseResourceError("rubric_resource_unavailable", 503) from None
    return data


__all__ = ["DIMENSION_IDS", "RUBRIC_FILE", "snapshot_rubric", "validate_rubric_snapshot"]
