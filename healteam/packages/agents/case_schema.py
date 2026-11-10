"""Validated case contract and explicit migration of the original v1 JSON.

Legacy names are accepted only here. Unknown names and ambiguous old/new pairs
are errors. Normalization copies mappings and never rewrites source case files.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from healteam_memory import School


NonEmptyText = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
NonEmptyTexts = Annotated[list[NonEmptyText], Field(min_length=1)]
SchoolList = Annotated[list[School], Field(min_length=1)]

_CASE_ID = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*\Z")
_WINDOW = re.compile(r"(\d+)~(\d+)\Z")
_RESERVED_WINDOWS_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def validate_case_id(case_id: str) -> str:
    """Accept a filename-safe identifier, never a user-supplied path."""
    if (
        not isinstance(case_id, str)
        or len(case_id) > 128
        or _CASE_ID.fullmatch(case_id) is None
        or case_id in _RESERVED_WINDOWS_NAMES
    ):
        raise ValueError("case_id must be a safe lowercase identifier of at most 128 characters")
    return case_id


def _rename(value: Any, names: dict[str, str]) -> Any:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    for legacy, canonical in names.items():
        if legacy in result:
            if canonical in result:
                raise ValueError(f"both {legacy!r} and {canonical!r} were supplied")
            result[canonical] = result.pop(legacy)
    return result


def _join_legacy_lines(value: Any, field: str) -> str:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a non-empty list of non-empty strings")
    return "\n".join(value)


class CaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DefensePattern(CaseModel):
    name: NonEmptyText
    description: NonEmptyText
    example: NonEmptyText
    underlying_fear: NonEmptyText | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_example(cls, value: Any) -> Any:
        return _rename(value, {"example_utterance": "example"})


class FamilyMember(CaseModel):
    name: NonEmptyText
    age: int = Field(ge=0, le=120, strict=True)
    gender: Literal["male", "female", "other", "unspecified"]
    occupation: NonEmptyText
    trait: NonEmptyText
    emotional_register: NonEmptyText
    defense_pattern: list[DefensePattern] = Field(min_length=1)
    origin_family_note: NonEmptyText | None = None
    education: NonEmptyText | None = None
    school_context_note: NonEmptyText | None = None


class Family(CaseModel):
    father: FamilyMember
    mother: FamilyMember
    patient: FamilyMember


class FamilyEmotions(CaseModel):
    father: NonEmptyText
    mother: NonEmptyText
    patient: NonEmptyText


class OpeningContext(CaseModel):
    scene: NonEmptyText
    atmosphere: NonEmptyText
    emotional_states: FamilyEmotions
    doctor_first_utterance_direction: NonEmptyText

    @model_validator(mode="before")
    @classmethod
    def migrate_opening(cls, value: Any) -> Any:
        result = _rename(value, {"session_round_0_scene": "scene", "emotional_state": "emotional_states"})
        if isinstance(result, dict) and "atmospheric_cues" in result:
            if "atmosphere" in result:
                raise ValueError("both 'atmospheric_cues' and 'atmosphere' were supplied")
            result = dict(result)
            result["atmosphere"] = _join_legacy_lines(result.pop("atmospheric_cues"), "atmospheric_cues")
        return result


class RoleHint(CaseModel):
    speaking_style: NonEmptyText
    first_utterance_draft: NonEmptyText

    @model_validator(mode="before")
    @classmethod
    def migrate_drafts(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "first_3_utterances_draft" not in value:
            return value
        result = dict(value)
        if "first_utterance_draft" in result:
            raise ValueError("both 'first_3_utterances_draft' and 'first_utterance_draft' were supplied")
        result["first_utterance_draft"] = _join_legacy_lines(result.pop("first_3_utterances_draft"), "first_3_utterances_draft")
        return result


class RoleHints(CaseModel):
    father: RoleHint
    mother: RoleHint
    patient: RoleHint

    @model_validator(mode="before")
    @classmethod
    def migrate_roles(cls, value: Any) -> Any:
        return _rename(value, {f"{role}_agent": role for role in ("father", "mother", "patient")})


class Milestone(CaseModel):
    id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    label: NonEmptyText
    description: NonEmptyText
    expected_window_rounds: NonEmptyText
    validation_signals: NonEmptyTexts
    primary_school: SchoolList

    @model_validator(mode="before")
    @classmethod
    def migrate_milestone(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = dict(value)
        if "label" in result and "description" not in result:
            result["description"] = result["label"]
        if "validation_signal" in result:
            if "validation_signals" in result:
                raise ValueError("both 'validation_signal' and 'validation_signals' were supplied")
            result["validation_signals"] = [result.pop("validation_signal")]
        return result

    @field_validator("expected_window_rounds")
    @classmethod
    def validate_window(cls, value: str) -> str:
        match = _WINDOW.fullmatch(value)
        if match is None or int(match[1]) > int(match[2]):
            raise ValueError("expected_window_rounds must be an ordered start~end range")
        return value


class Subsystems(CaseModel):
    marital_subsystem: NonEmptyText
    parental_subsystem: NonEmptyText
    parent_child_subsystem: NonEmptyText
    sibling_subsystem: NonEmptyText


class Boundary(CaseModel):
    type: NonEmptyText
    description: NonEmptyText


class Boundaries(CaseModel):
    father_mother: Boundary
    mother_patient: Boundary
    father_patient: Boundary


class StructuralAnalysis(CaseModel):
    subsystems: Subsystems
    boundaries: Boundaries
    triangles: NonEmptyTexts


class BowenAnalysis(CaseModel):
    differentiation_of_self: FamilyEmotions
    family_projection_process: NonEmptyText
    multigenerational_transmission: NonEmptyText
    triangulation: NonEmptyText
    sibling_position: NonEmptyText


class CommunicationStances(CaseModel):
    father_default: NonEmptyText
    mother_default: NonEmptyText
    patient_default: NonEmptyText


class SatirAnalysis(CaseModel):
    communication_stances: CommunicationStances
    family_life_process_disruption: NonEmptyText
    iceberg_dynamics: NonEmptyText
    congruence_level: NonEmptyText


class FamilyPattern(CaseModel):
    structural: StructuralAnalysis
    bowen: BowenAnalysis
    satir: SatirAnalysis

    @model_validator(mode="before")
    @classmethod
    def migrate_schools(cls, value: Any) -> Any:
        return _rename(value, {f"{school.value}_analysis": school.value for school in School})


class TreatmentFocus(CaseModel):
    id: NonEmptyText
    title: NonEmptyText
    area: NonEmptyText
    description: NonEmptyText
    primary_school_affinity: SchoolList

    @model_validator(mode="before")
    @classmethod
    def migrate_area(cls, value: Any) -> Any:
        if isinstance(value, dict) and "title" in value and "area" not in value:
            return {**value, "area": value["title"]}
        return value


class SchoolSuitability(CaseModel):
    suitability: Literal["high", "medium", "low"]
    rationale: NonEmptyText
    key_techniques_to_apply: NonEmptyTexts


class SchoolAffinity(CaseModel):
    structural: SchoolSuitability
    bowen: SchoolSuitability
    satir: SchoolSuitability


class ClinicalNotes(CaseModel):
    diagnosis_considerations: NonEmptyTexts
    contraindications: NonEmptyTexts
    supervision_focus_areas: NonEmptyTexts
    references: NonEmptyTexts


class CaseDefinition(CaseModel):
    case_id: str
    version: Annotated[str, StringConstraints(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")]
    title: NonEmptyText
    subtitle: NonEmptyText
    language: NonEmptyText
    archetype: NonEmptyText
    family_system_pattern: NonEmptyText
    summary: NonEmptyText
    presenting_problem: NonEmptyText
    duration_weeks: int = Field(ge=0, strict=True)
    tier: NonEmptyText
    family: Family
    family_pattern: FamilyPattern
    treatment_focus: list[TreatmentFocus] = Field(min_length=1)
    opening_context: OpeningContext
    expected_milestones: list[Milestone] = Field(min_length=1)
    school_affinity: SchoolAffinity
    clinical_notes: ClinicalNotes
    agent_role_hints: RoleHints

    @field_validator("case_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return validate_case_id(value)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> CaseDefinition:
        milestone_ids = [item.id for item in self.expected_milestones]
        if len(milestone_ids) != len(set(milestone_ids)):
            raise ValueError("milestone IDs must be unique within a case")
        focus_ids = [item.id for item in self.treatment_focus]
        if len(focus_ids) != len(set(focus_ids)):
            raise ValueError("treatment focus IDs must be unique within a case")
        return self


__all__ = ["CaseDefinition", "validate_case_id"]
