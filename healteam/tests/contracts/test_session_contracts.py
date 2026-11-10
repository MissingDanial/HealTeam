from __future__ import annotations

from uuid import uuid4

import pytest
from healteam_memory.contracts import (
    CreateSessionCommand,
    EventEnvelope,
    ReportState,
    SessionSnapshot,
    SessionState,
    SubmitMessageCommand,
    transition_snapshot,
)
from pydantic import ValidationError


def snapshot(**updates) -> SessionSnapshot:
    return SessionSnapshot(session_id=uuid4(), case_id="teen-truancy-01", case_version="1.0", **updates)


def test_training_is_the_default_of_the_future_create_contract() -> None:
    command = CreateSessionCommand(case_id="teen-truancy-01", school="satir", client_request_id=uuid4())
    assert command.mode.value == "training"
    assert command.max_rounds == 10


@pytest.mark.parametrize(
    "changes",
    [
        {"case_id": "../private"},
        {"school": "other"},
        {"mode": "unknown"},
        {"max_rounds": 0},
        {"max_rounds": 31},
        {"max_rounds": True},
        {"api_key": "secret"},
        {"case_id": "con"},
    ],
)
def test_invalid_create_commands_are_rejected(changes) -> None:
    data = {"case_id": "teen-truancy-01", "school": "satir", "client_request_id": str(uuid4()), **changes}
    with pytest.raises(ValidationError):
        CreateSessionCommand.model_validate(data)


def test_message_contract_strips_whitespace_and_requires_next_turn() -> None:
    message = SubmitMessageCommand(client_message_id=uuid4(), expected_turn=1, content="  请你先说说。  ")
    assert message.content == "请你先说说。"
    with pytest.raises(ValidationError):
        SubmitMessageCommand(client_message_id=uuid4(), expected_turn=0, content=" ")


def test_completion_requires_a_saved_report() -> None:
    active = snapshot(state="running", phase="report")
    with pytest.raises(ValidationError):
        transition_snapshot(active, SessionState.COMPLETED)
    complete = transition_snapshot(active, SessionState.COMPLETED, report_state=ReportState.READY)
    assert complete.report_state == ReportState.READY
    assert active.state == SessionState.RUNNING


def test_committed_cancellation_prevents_later_completion() -> None:
    active = snapshot(state="running", phase="report")
    cancelling = transition_snapshot(active, SessionState.CANCELLING)
    with pytest.raises(ValueError, match="transition"):
        transition_snapshot(cancelling, SessionState.COMPLETED, report_state=ReportState.READY)
    assert (
        transition_snapshot(cancelling, SessionState.CANCELLED, report_state=ReportState.CANCELLED).state
        == SessionState.CANCELLED
    )


def test_only_failed_review_can_retry_without_replaying_the_therapy() -> None:
    failed_review = snapshot(state="failed", phase="supervision", report_state="failed")
    assert transition_snapshot(failed_review, SessionState.RUNNING).phase.value == "supervision"
    with pytest.raises(ValueError, match="review"):
        transition_snapshot(snapshot(state="failed", phase="simulation"), SessionState.RUNNING)


@pytest.mark.parametrize("phase", ["supervision", "report"])
def test_interrupted_review_can_retry_only_from_the_saved_phase(phase) -> None:
    interrupted = snapshot(state="interrupted", phase=phase, report_state="failed")
    retry = transition_snapshot(
        interrupted,
        SessionState.RUNNING,
        report_state=ReportState.GENERATING if phase == "report" else ReportState.FAILED,
    )
    assert retry.phase.value == phase
    with pytest.raises(ValueError):
        transition_snapshot(interrupted, SessionState.RUNNING, phase="simulation")


@pytest.mark.parametrize(
    "changes",
    [
        {"phase": "simulation", "report_state": "not_started"},
        {"phase": "report", "report_state": "cancelled"},
        {"phase": "supervision", "report_state": "not_started"},
    ],
)
def test_interrupted_therapy_or_nonfailed_review_cannot_restart(changes) -> None:
    interrupted = snapshot(state="interrupted", **changes)
    with pytest.raises(ValueError, match="terminal"):
        transition_snapshot(interrupted, SessionState.RUNNING)


def test_demo_cannot_enter_waiting_for_learner_input() -> None:
    with pytest.raises(ValidationError):
        transition_snapshot(snapshot(mode="demo"), SessionState.AWAITING_INPUT)


def test_private_hints_cannot_be_encoded_as_public_events() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope(
            session_id=uuid4(),
            seq=1,
            round=1,
            type="hint",
            actor="observer",
            source="ai",
            visibility="public",
            payload={"content": "private"},
        )


def test_human_input_cannot_be_attributed_to_an_ai_role() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope(
            session_id=uuid4(),
            seq=1,
            round=1,
            type="utterance",
            actor="doctor",
            source="human",
            visibility="public",
            payload={"content": "hello"},
        )


def test_review_phase_cannot_rewind_to_simulation() -> None:
    reviewing = snapshot(state="running", phase="report", report_state="generating")
    with pytest.raises(ValueError, match="phase"):
        transition_snapshot(
            reviewing, SessionState.RUNNING, phase="simulation", report_state=ReportState.NOT_STARTED
        )


def test_terminal_snapshot_is_immutable_but_identical_transition_is_idempotent() -> None:
    cancelled = snapshot(state="cancelled", report_state="cancelled")
    assert transition_snapshot(cancelled, SessionState.CANCELLED) == cancelled
    with pytest.raises(ValueError, match="terminal"):
        transition_snapshot(cancelled, SessionState.CANCELLED, report_state=ReportState.GENERATING)


def test_waiting_for_input_cannot_happen_during_review() -> None:
    with pytest.raises(ValidationError):
        snapshot(state="awaiting_input", phase="report")


def test_system_events_cannot_impersonate_an_ai_room_participant() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope(
            session_id=uuid4(),
            seq=1,
            round=1,
            type="utterance",
            actor="doctor",
            source="system",
            visibility="public",
            payload={"content": "hello"},
        )


def test_empty_environment_root_uses_source_default(monkeypatch) -> None:
    from healteam_memory.resources import find_project_root

    monkeypatch.setenv("HEALTEAM_ROOT", "")
    assert (find_project_root() / "data" / "cases" / "teen-truancy-01.json").is_file()


@pytest.mark.parametrize("kind", ["model_call_started", "model_call_finished"])
@pytest.mark.parametrize("visibility", ["public", "learner"])
def test_model_accounting_cannot_be_published_as_a_room_event(kind, visibility):
    with pytest.raises(ValidationError, match="internal system"):
        EventEnvelope(
            session_id=uuid4(),
            seq=1,
            round=0,
            type=kind,
            actor="system",
            source="system",
            visibility=visibility,
            payload={},
        )
