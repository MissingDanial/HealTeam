"""Public practice views excluding case secrets and model credentials."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from healteam_memory.contracts import (
    ContractModel,
    EventEnvelope,
    ReportState,
    SessionMode,
    SessionSnapshot,
    UsageSummary,
)
from healteam_memory.evaluation_contracts import Assessment
from healteam_memory.schema import School
from pydantic import Field


class SessionSummary(SessionSnapshot):
    completed_rounds: int = Field(ge=0, le=30)
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None
    provider: str | None = None
    model: str | None = None
    usage: UsageSummary = Field(default_factory=UsageSummary)


class SessionView(SessionSummary):
    events: list[EventEnvelope]


class SessionHistory(ContractModel):
    items: list[SessionSummary]
    total: int = Field(ge=0)


class MessageReceipt(ContractModel):
    session_id: UUID
    client_message_id: UUID
    accepted_turn: int = Field(ge=1, le=30)
    state: Literal["running"]


class ReviewPoint(ContractModel):
    text: str = Field(min_length=1, max_length=1200)
    evidence_seq: list[int] = Field(min_length=1, max_length=12)


class AIReview(ContractModel):
    summary: str = Field(min_length=1, max_length=2000)
    observations: list[ReviewPoint] = Field(min_length=1, max_length=8)
    next_steps: list[ReviewPoint] = Field(min_length=1, max_length=5)


class WorkflowReport(ContractModel):
    session_id: UUID
    status: ReportState
    content: str | None
    kind: Literal["workflow_check", "ai_review"]
    generated_at: datetime | None
    school: School
    mode: SessionMode
    structured: AIReview | None = None
    rubric: dict | None = None
    assessment: Assessment | None = None
