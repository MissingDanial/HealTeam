"""Trial training observations and local human review; no certification claims."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import ContractModel

Rating = Annotated[int, Field(strict=True, ge=0, le=2)]
EvidenceSeq = Annotated[int, Field(strict=True, ge=1)]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DimensionId(StrEnum):
    PARTICIPATION = "participation"
    EXPLORATION = "exploration"
    SCHOOL_CONSISTENCY = "school_consistency"
    COLLABORATIVE_NEXT_STEP = "collaborative_next_step"


class DimensionRating(ContractModel):
    dimension_id: DimensionId
    rating: Rating | None
    rationale: str = Field(min_length=1, max_length=1200)
    evidence_seq: list[EvidenceSeq] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def check_evidence(self) -> Self:
        if len(self.evidence_seq) != len(set(self.evidence_seq)):
            raise ValueError("evidence sequences must be unique")
        if self.rating is not None and not self.evidence_seq:
            raise ValueError("an observed rating needs dialogue evidence")
        return self


class MilestoneRating(ContractModel):
    milestone_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    status: Literal["observed", "not_observed", "insufficient_evidence"]
    rationale: str = Field(min_length=1, max_length=1200)
    evidence_seq: list[EvidenceSeq] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def check_evidence(self) -> Self:
        if len(self.evidence_seq) != len(set(self.evidence_seq)):
            raise ValueError("evidence sequences must be unique")
        if self.status == "observed" and not self.evidence_seq:
            raise ValueError("an observed milestone needs dialogue evidence")
        return self


class EvaluationValues(ContractModel):
    dimensions: list[DimensionRating] = Field(min_length=4, max_length=4)
    milestones: list[MilestoneRating] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def check_sets(self) -> Self:
        if {item.dimension_id for item in self.dimensions} != set(DimensionId):
            raise ValueError("each of the four dimensions must appear exactly once")
        ids = [item.milestone_id for item in self.milestones]
        if len(ids) != len(set(ids)):
            raise ValueError("milestone IDs must be unique")
        return self


class Assessment(EvaluationValues):
    rubric_version: str = Field(min_length=1, max_length=64)
    rubric_hash: Fingerprint
    reviewed_event_seq: list[EvidenceSeq] = Field(min_length=1, max_length=3000)
    total_utterances: int = Field(strict=True, ge=1, le=3000)

    @model_validator(mode="after")
    def check_scope(self) -> Self:
        if self.reviewed_event_seq != sorted(set(self.reviewed_event_seq)):
            raise ValueError("reviewed sequences must be unique and ordered")
        if len(self.reviewed_event_seq) > self.total_utterances:
            raise ValueError("review window cannot exceed the saved transcript")
        scope = set(self.reviewed_event_seq)
        if any(not set(item.evidence_seq) <= scope for item in (*self.dimensions, *self.milestones)):
            raise ValueError("assessment evidence must belong to the reviewed window")
        return self


class ReviewSubmission(EvaluationValues):
    client_review_id: UUID
    expected_revision: int = Field(strict=True, ge=0)
    report_hash: Fingerprint
    rubric_hash: Fingerprint
    reviewer_name: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=2000)


class ReviewRecord(EvaluationValues):
    review_id: UUID
    session_id: UUID
    revision: int = Field(strict=True, ge=1)
    created_at: datetime
    client_review_id: UUID
    report_hash: Fingerprint
    rubric_hash: Fingerprint
    reviewer_name: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=2000)
    reviewer_identity_verified: Literal[False] = False


class ReviewHistory(ContractModel):
    session_id: UUID
    report_hash: Fingerprint
    rubric_hash: Fingerprint
    latest_revision: int = Field(strict=True, ge=0)
    items: list[ReviewRecord]


class CalibrationGroup(ContractModel):
    rubric_hash: Fingerprint
    rubric_version: str
    school: str
    provider: str | None
    model: str | None
    reviewed_sessions: int = Field(ge=0)
    paired_dimensions: int = Field(ge=0)
    exact_matches: int = Field(ge=0)
    exact_agreement: float | None = Field(default=None, ge=0, le=1)
    insufficient_pairs: int = Field(ge=0)
    out_of_window_pairs: int = Field(default=0, ge=0)


class CalibrationSummary(ContractModel):
    status: Literal["awaiting_expert_calibration"] = "awaiting_expert_calibration"
    total_sessions: int = Field(ge=0)
    eligible_sessions: int = Field(ge=0)
    reviewed_sessions: int = Field(ge=0)
    paired_dimensions: int = Field(ge=0)
    exact_matches: int = Field(ge=0)
    exact_agreement: float | None = Field(default=None, ge=0, le=1)
    insufficient_pairs: int = Field(ge=0)
    out_of_window_pairs: int = Field(default=0, ge=0)
    excluded_demo_sessions: int = Field(ge=0)
    groups: list[CalibrationGroup]
