"""Shared session contracts used by the durable local practice workflow.

The retained S0 adapter is separate from these resources and disabled in the
normal application. Persistence and transition checks implement this boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Self
from uuid import UUID, uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .schema import Actor, School

CONTRACT_VERSION = "0.5.0"


def _safe_case_id(value: str) -> str:
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    if value in reserved:
        raise ValueError("case_id cannot be a reserved filename")
    return value


CaseId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$", max_length=128),
    AfterValidator(_safe_case_id),
]
RoundLimit = Annotated[int, Field(strict=True, ge=1, le=30)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SessionMode(StrEnum):
    TRAINING = "training"
    DEMO = "demo"


class ModelMode(StrEnum):
    MOCK = "mock"
    LIVE = "live"


class SessionState(StrEnum):
    CREATED = "created"
    AWAITING_INPUT = "awaiting_input"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class SessionPhase(StrEnum):
    SIMULATION = "simulation"
    SUPERVISION = "supervision"
    REPORT = "report"


class ReportState(StrEnum):
    NOT_STARTED = "not_started"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventSource(StrEnum):
    HUMAN = "human"
    AI = "ai"
    SYSTEM = "system"


class EventVisibility(StrEnum):
    PUBLIC = "public"
    LEARNER = "learner"
    SUPERVISOR = "supervisor"
    INTERNAL = "internal"


class StreamEventType(StrEnum):
    STATUS_CHANGED = "status_changed"
    UTTERANCE = "utterance"
    HINT = "hint"
    REPORT_READY = "report_ready"
    ERROR = "error"
    MODEL_CALL_STARTED = "model_call_started"
    MODEL_CALL_FINISHED = "model_call_finished"


class CreateSessionCommand(ContractModel):
    client_request_id: UUID
    case_id: CaseId
    school: School
    mode: SessionMode = SessionMode.TRAINING
    max_rounds: RoundLimit = 10


class SubmitMessageCommand(ContractModel):
    client_message_id: UUID
    expected_turn: RoundLimit
    content: str = Field(min_length=1, max_length=2000)


class UsageSummary(ContractModel):
    requests: int = Field(default=0, strict=True, ge=0)
    input_tokens: int = Field(default=0, strict=True, ge=0)
    output_tokens: int = Field(default=0, strict=True, ge=0)
    unknown_requests: int = Field(default=0, strict=True, ge=0)
    reserved_tokens: int = Field(default=0, strict=True, ge=0)
    max_calls: int = Field(default=0, strict=True, ge=0)
    token_budget: int = Field(default=0, strict=True, ge=0)


class SessionSnapshot(ContractModel):
    session_id: UUID
    case_id: CaseId
    case_version: str = Field(min_length=1, max_length=64)
    school: School = School.STRUCTURAL
    mode: SessionMode = SessionMode.TRAINING
    model_mode: ModelMode = ModelMode.MOCK
    state: SessionState = SessionState.CREATED
    phase: SessionPhase = SessionPhase.SIMULATION
    max_rounds: RoundLimit = 10
    current_round: int = Field(default=0, strict=True, ge=0, le=30)
    last_event_seq: int = Field(default=0, strict=True, ge=0)
    report_state: ReportState = ReportState.NOT_STARTED

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        if self.current_round > self.max_rounds:
            raise ValueError("current_round cannot exceed max_rounds")
        if self.mode == SessionMode.DEMO and self.state == SessionState.AWAITING_INPUT:
            raise ValueError("demo sessions cannot wait for a learner message")
        if (
            self.state in {SessionState.CREATED, SessionState.AWAITING_INPUT}
            and self.phase != SessionPhase.SIMULATION
        ):
            raise ValueError("preparation and learner input belong to the simulation phase")
        if self.state == SessionState.COMPLETED:
            if self.report_state != ReportState.READY or self.phase != SessionPhase.REPORT:
                raise ValueError("completion requires a saved report in the report phase")
        if (
            self.state in {SessionState.CANCELLED, SessionState.CANCELLING}
            and self.report_state == ReportState.READY
        ):
            raise ValueError("a committed ready report cannot be cancelled")
        if self.report_state == ReportState.READY and self.state != SessionState.COMPLETED:
            raise ValueError("a ready report and completion must be committed together")
        if self.phase == SessionPhase.SIMULATION and self.report_state in {
            ReportState.GENERATING,
            ReportState.READY,
        }:
            raise ValueError("report generation cannot precede review")
        return self


class EventEnvelope(ContractModel):
    event_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    seq: int = Field(strict=True, ge=1)
    round: int = Field(strict=True, ge=0, le=30)
    type: StreamEventType
    actor: Actor
    source: EventSource
    visibility: EventVisibility
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any]

    @model_validator(mode="after")
    def check_attribution(self) -> Self:
        if self.type in {StreamEventType.MODEL_CALL_STARTED, StreamEventType.MODEL_CALL_FINISHED} and (
            self.actor != Actor.SYSTEM
            or self.source != EventSource.SYSTEM
            or self.visibility != EventVisibility.INTERNAL
        ):
            raise ValueError("model accounting events must be internal system events")
        if self.type == StreamEventType.HINT and self.visibility == EventVisibility.PUBLIC:
            raise ValueError("observer hints are private events")
        if self.type == StreamEventType.HINT and (
            self.actor != Actor.OBSERVER or self.source != EventSource.AI
        ):
            raise ValueError("observer hints require the observer AI as their source")
        if self.type == StreamEventType.UTTERANCE:
            if self.round == 0:
                raise ValueError("room utterances start at round one")
            if self.visibility != EventVisibility.PUBLIC:
                raise ValueError("room utterances must be public")
            if not isinstance(self.payload.get("content"), str) or not self.payload["content"].strip():
                raise ValueError("utterances require non-empty content")
            if self.source == EventSource.HUMAN and self.actor != Actor.TRAINEE:
                raise ValueError("human learner input must be attributed to trainee")
            if self.actor == Actor.TRAINEE and self.source != EventSource.HUMAN:
                raise ValueError("AI output cannot impersonate a human learner")
            if self.actor != Actor.TRAINEE and self.source != EventSource.AI:
                raise ValueError("simulated room participants must be attributed to AI")
            if self.actor not in {Actor.TRAINEE, Actor.DOCTOR, Actor.FATHER, Actor.MOTHER, Actor.PATIENT}:
                raise ValueError("only room participants can emit utterances")
        return self


_TRANSITIONS = {
    SessionState.CREATED: {
        SessionState.AWAITING_INPUT,
        SessionState.RUNNING,
        SessionState.CANCELLING,
        SessionState.FAILED,
        SessionState.INTERRUPTED,
    },
    SessionState.AWAITING_INPUT: {SessionState.RUNNING, SessionState.CANCELLING, SessionState.FAILED},
    SessionState.RUNNING: {
        SessionState.AWAITING_INPUT,
        SessionState.COMPLETED,
        SessionState.CANCELLING,
        SessionState.FAILED,
        SessionState.INTERRUPTED,
    },
    SessionState.CANCELLING: {SessionState.CANCELLED, SessionState.INTERRUPTED},
    SessionState.FAILED: {SessionState.RUNNING},
    SessionState.COMPLETED: set(),
    SessionState.CANCELLED: set(),
    SessionState.INTERRUPTED: {SessionState.RUNNING},
}


def transition_snapshot(
    snapshot: SessionSnapshot,
    next_state: SessionState,
    *,
    phase: SessionPhase | None = None,
    report_state: ReportState | None = None,
) -> SessionSnapshot:
    """Validate a transition committed with a database CAS/transaction.

    Pure validation cannot resolve races on its own. In particular, cancelling
    and completing must compare the stored state in the same transaction that
    saves the terminal event and report.
    """
    next_state = SessionState(next_state)
    next_phase = snapshot.phase if phase is None else SessionPhase(phase)
    next_report = snapshot.report_state if report_state is None else ReportState(report_state)
    retrying_interrupted_review = (
        snapshot.state == SessionState.INTERRUPTED
        and next_state == SessionState.RUNNING
        and snapshot.phase in {SessionPhase.SUPERVISION, SessionPhase.REPORT}
        and snapshot.report_state == ReportState.FAILED
        and next_phase == snapshot.phase
    )
    if snapshot.state in {SessionState.COMPLETED, SessionState.CANCELLED} or (
        snapshot.state == SessionState.INTERRUPTED and not retrying_interrupted_review
    ):
        if (
            next_state != snapshot.state
            or next_phase != snapshot.phase
            or next_report != snapshot.report_state
        ):
            raise ValueError("terminal session snapshots cannot change")
        return snapshot
    if next_state != snapshot.state and next_state not in _TRANSITIONS[snapshot.state]:
        raise ValueError(f"illegal session transition: {snapshot.state.value} -> {next_state.value}")
    phase_order = {SessionPhase.SIMULATION: 0, SessionPhase.SUPERVISION: 1, SessionPhase.REPORT: 2}
    distance = phase_order[next_phase] - phase_order[snapshot.phase]
    if distance < 0 or distance > 1 or (distance == 1 and next_state != SessionState.RUNNING):
        raise ValueError("illegal phase transition")
    if snapshot.state == SessionState.CANCELLING and next_phase != snapshot.phase:
        raise ValueError("cancellation cannot advance the phase")
    if next_report == ReportState.NOT_STARTED and snapshot.report_state != ReportState.NOT_STARTED:
        raise ValueError("report state cannot reset to not_started")
    if snapshot.report_state == ReportState.CANCELLED and next_report != ReportState.CANCELLED:
        raise ValueError("cancelled report state cannot change")
    if (
        snapshot.state in {SessionState.FAILED, SessionState.INTERRUPTED}
        and next_state == SessionState.RUNNING
    ):
        if snapshot.phase not in {SessionPhase.SUPERVISION, SessionPhase.REPORT}:
            raise ValueError("only a failed review can be retried in the same session")
        if phase is not None and SessionPhase(phase) != snapshot.phase:
            raise ValueError("retry must resume the saved review phase")
    data = snapshot.model_dump()
    data["state"] = next_state
    if phase is not None:
        data["phase"] = phase
    if report_state is not None:
        data["report_state"] = report_state
    return SessionSnapshot.model_validate(data)
