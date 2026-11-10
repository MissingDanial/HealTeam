"""Durable repository behavior, with temporary file databases and no model calls."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from healteam_memory.contracts import CreateSessionCommand, SessionState, transition_snapshot
from healteam_memory.training_store import (
    BusyError,
    ConflictError,
    EventDraft,
    Mutation,
    SchemaVersionError,
    TrainingRepository,
)
from sqlalchemy.exc import OperationalError


def command(**changes):
    return CreateSessionCommand(
        client_request_id=uuid4(), case_id="teen-truancy-01", school="satir", **changes
    )


async def create(repo, request=None):
    return await repo.create(
        request or command(),
        config_snapshot={"model_mode": "mock", "temperature": 0.2},
        case_snapshot={"case_id": "teen-truancy-01", "version": "1.0.0", "title": "Stored case"},
    )


def status(snapshot):
    return EventDraft(
        round=snapshot.current_round,
        type="status_changed",
        actor="system",
        source="system",
        visibility="public",
        payload={"snapshot": snapshot.model_dump(mode="json")},
    )


async def activate(repo, session_id):
    def update(record):
        target = transition_snapshot(record.snapshot, SessionState.AWAITING_INPUT)
        return Mutation(snapshot=target, events=[status(target)])

    return await repo.transact(session_id, update)


def test_file_database_preserves_case_configuration_events_and_snapshot_after_reopen(tmp_path):
    async def scenario():
        path = tmp_path / "training.sqlite3"
        repo = TrainingRepository(path)
        await repo.initialize()
        initial = await create(repo)
        active = await activate(repo, initial.record.snapshot.session_id)
        await repo.dispose()
        reopened = TrainingRepository(path)
        restored = await reopened.get(initial.record.snapshot.session_id)
        assert restored.snapshot == active.record.snapshot
        assert restored.case_snapshot["title"] == "Stored case"
        assert restored.config_snapshot["temperature"] == 0.2
        assert [event.seq for event in restored.events] == [1, 2]
        assert restored.created_at == initial.record.created_at
        await reopened.dispose()

    asyncio.run(scenario())


def test_create_replay_precedes_active_owner_check_and_rejects_different_content(tmp_path):
    async def scenario():
        repo = TrainingRepository(tmp_path / "training.sqlite3")
        request = command()
        first = await create(repo, request)
        replay = await create(repo, request)
        assert replay.replayed
        assert replay.record.snapshot.session_id == first.record.snapshot.session_id
        with pytest.raises(ConflictError):
            await create(repo, request.model_copy(update={"max_rounds": request.max_rounds + 1}))
        with pytest.raises(BusyError):
            await create(repo)
        await activate(repo, first.record.snapshot.session_id)
        with pytest.raises(BusyError):
            await create(repo)
        await repo.dispose()

    asyncio.run(scenario())


def test_lookup_create_checks_original_request_without_claiming_or_changing_a_session(tmp_path):
    async def scenario():
        repo = TrainingRepository(tmp_path / "training.sqlite3")
        request = command()
        assert await repo.lookup_create(request) is None
        assert (await repo.list_sessions())[1] == 0
        first = (await create(repo, request)).record
        waiting = (await activate(repo, first.snapshot.session_id)).record
        found = await repo.lookup_create(request)
        assert found == waiting
        assert (await repo.get(first.snapshot.session_id)).revision == waiting.revision
        with pytest.raises(ConflictError) as error:
            await repo.lookup_create(
                CreateSessionCommand.model_validate({**request.model_dump(), "school": "bowen"})
            )
        assert error.value.session_id == first.snapshot.session_id
        assert (await repo.list_sessions())[1] == 1
        await repo.dispose()

    asyncio.run(scenario())


def test_message_receipt_replays_before_terminal_checks_and_survives_reopen(tmp_path):
    async def scenario():
        path = tmp_path / "training.sqlite3"
        repo = TrainingRepository(path)
        initial = await create(repo)
        session_id = initial.record.snapshot.session_id
        await activate(repo, session_id)
        receipt = {"session_id": str(session_id), "accepted_turn": 1}

        def submit(record):
            target = transition_snapshot(record.snapshot, SessionState.RUNNING).model_copy(
                update={"current_round": 1}
            )
            utterance = EventDraft(
                round=1,
                type="utterance",
                actor="trainee",
                source="human",
                visibility="public",
                payload={"content": "Please tell me more"},
            )
            return Mutation(snapshot=target, events=[utterance, status(target)], receipt=receipt)

        accepted = await repo.transact(
            session_id, submit, receipt_key="message-1", receipt_payload={"content": "Please tell me more"}
        )
        await repo.transact(
            session_id,
            lambda record: Mutation(
                snapshot=transition_snapshot(record.snapshot, SessionState.FAILED), error_code="test_failure"
            ),
        )
        await repo.dispose()
        reopened = TrainingRepository(path)

        def forbidden(record):
            raise AssertionError("duplicate receipts must not execute terminal/busy checks")

        replay = await reopened.transact(
            session_id, forbidden, receipt_key="message-1", receipt_payload={"content": "Please tell me more"}
        )
        assert replay.replayed and replay.receipt == accepted.receipt == receipt
        assert len(replay.record.events) == len(accepted.record.events)
        with pytest.raises(ConflictError):
            await reopened.transact(
                session_id, forbidden, receipt_key="message-1", receipt_payload={"content": "different"}
            )
        await reopened.dispose()

    asyncio.run(scenario())


def test_mutation_failure_rolls_back_snapshot_events_and_receipt(tmp_path):
    async def scenario():
        repo = TrainingRepository(tmp_path / "training.sqlite3")
        first = (await create(repo)).record

        def invalid(record):
            target = transition_snapshot(record.snapshot, SessionState.AWAITING_INPUT)
            bad = EventDraft(
                round=0,
                type="utterance",
                actor="trainee",
                source="human",
                visibility="public",
                payload={"content": "invalid round zero"},
            )
            return Mutation(snapshot=target, events=[status(target), bad], receipt={"bad": True})

        with pytest.raises(ValueError):
            await repo.transact(
                first.snapshot.session_id, invalid, receipt_key="bad", receipt_payload={"content": "x"}
            )
        restored = await repo.get(first.snapshot.session_id)
        assert restored.snapshot == first.snapshot
        assert restored.events == first.events
        good = await repo.transact(
            first.snapshot.session_id,
            lambda record: Mutation(
                snapshot=transition_snapshot(record.snapshot, SessionState.AWAITING_INPUT)
            ),
            receipt_key="bad",
            receipt_payload={"content": "x"},
        )
        assert not good.replayed
        await repo.dispose()

    asyncio.run(scenario())


def test_two_repository_instances_cannot_create_two_active_sessions(tmp_path):
    async def scenario():
        path = tmp_path / "training.sqlite3"
        left, right = TrainingRepository(path), TrainingRepository(path)
        await left.initialize()
        await right.initialize()
        outcomes = await asyncio.gather(create(left), create(right), return_exceptions=True)
        assert sum(not isinstance(item, Exception) for item in outcomes) == 1
        assert sum(isinstance(item, BusyError) for item in outcomes) == 1
        records, total = await left.list_sessions()
        assert len(records) == total == 1
        await left.dispose()
        await right.dispose()

    asyncio.run(scenario())


def test_concurrent_mutations_do_not_lose_events_and_cursor_matches_snapshot(tmp_path):
    async def scenario():
        path = tmp_path / "training.sqlite3"
        left, right = TrainingRepository(path), TrainingRepository(path)
        session_id = (await create(left)).record.snapshot.session_id
        await activate(left, session_id)

        def append(record):
            return Mutation(snapshot=record.snapshot, events=[status(record.snapshot)])

        await asyncio.gather(*(repo.transact(session_id, append) for repo in [left, right] * 5))
        snapshot, events = await left.events(session_id, after_seq=3)
        assert snapshot.last_event_seq == 12
        assert [event.seq for event in events] == list(range(4, 13))
        assert events[-1].payload["snapshot"]["last_event_seq"] == snapshot.last_event_seq
        await left.dispose()
        await right.dispose()

    asyncio.run(scenario())


def test_startup_recovery_interrupts_inflight_but_retains_waiting_session(tmp_path):
    async def scenario():
        path = tmp_path / "training.sqlite3"
        repo = TrainingRepository(path)
        session_id = (await create(repo)).record.snapshot.session_id
        assert await repo.recover_interrupted() == 1
        interrupted = await repo.get(session_id)
        assert interrupted.snapshot.state == SessionState.INTERRUPTED
        assert interrupted.error_code == "process_interrupted"
        assert interrupted.events[-1].payload["snapshot"]["state"] == "interrupted"
        assert await repo.recover_interrupted() == 0
        waiting_id = (await create(repo)).record.snapshot.session_id
        await activate(repo, waiting_id)
        await repo.dispose()
        reopened = TrainingRepository(path)
        assert await reopened.recover_interrupted() == 0
        assert (await reopened.get(waiting_id)).snapshot.state == SessionState.AWAITING_INPUT
        await reopened.dispose()

    asyncio.run(scenario())


def test_unknown_schema_version_is_rejected_without_changing_database(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    async def scenario():
        repo = TrainingRepository(path)
        with pytest.raises(SchemaVersionError):
            await repo.initialize()
        await repo.dispose()

    asyncio.run(scenario())
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_empty_database_is_migrated_once_and_unversioned_existing_data_is_not_repurposed(tmp_path):
    async def scenario():
        path = tmp_path / "empty.sqlite3"
        repo = TrainingRepository(path)
        await repo.initialize()
        await repo.initialize()
        await repo.dispose()
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        unrelated = tmp_path / "unrelated.sqlite3"
        with sqlite3.connect(unrelated) as connection:
            connection.execute("CREATE TABLE important_data (value TEXT)")
            connection.execute("INSERT INTO important_data VALUES ('preserve me')")
        other = TrainingRepository(unrelated)
        with pytest.raises(SchemaVersionError):
            await other.initialize()
        await other.dispose()
        with sqlite3.connect(unrelated) as connection:
            assert connection.execute("SELECT value FROM important_data").fetchone()[0] == "preserve me"
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 0

    asyncio.run(scenario())


def test_noop_preserves_revision_and_update_time(tmp_path):
    async def scenario():
        repo = TrainingRepository(tmp_path / "training.sqlite3")
        original = (await create(repo)).record
        result = await repo.transact(
            original.snapshot.session_id,
            lambda record: Mutation(snapshot=record.snapshot, error_code=record.error_code),
        )
        assert result.record == original
        restored = await repo.get(original.snapshot.session_id)
        assert restored.revision == original.revision
        assert restored.updated_at == original.updated_at
        await repo.dispose()

    asyncio.run(scenario())


async def reporting_session(repo):
    session_id = (await create(repo)).record.snapshot.session_id

    def start(record):
        target = transition_snapshot(record.snapshot, SessionState.RUNNING).model_copy(
            update={"current_round": 1}
        )
        return Mutation(snapshot=target, events=[status(target)])

    await repo.transact(session_id, start)

    def review(record):
        target = transition_snapshot(record.snapshot, SessionState.RUNNING, phase="supervision")
        return Mutation(snapshot=target, events=[status(target)], completed_rounds=1)

    await repo.transact(session_id, review)
    await repo.transact(
        session_id,
        lambda record: Mutation(
            snapshot=transition_snapshot(
                record.snapshot, SessionState.RUNNING, phase="report", report_state="generating"
            )
        ),
    )
    return session_id


def test_report_state_events_and_document_commit_or_rollback_together(tmp_path):
    async def scenario():
        path = tmp_path / "training.sqlite3"
        repo = TrainingRepository(path)
        session_id = await reporting_session(repo)
        before = await repo.get(session_id)
        report = {
            "session_id": str(session_id),
            "status": "ready",
            "content": "Workflow check",
            "kind": "workflow_check",
        }

        def finish(record, duplicate=False):
            target = transition_snapshot(record.snapshot, SessionState.COMPLETED, report_state="ready")
            draft = EventDraft(
                round=1,
                type="report_ready",
                actor="system",
                source="system",
                visibility="public",
                payload={"report": report},
                event_id=record.events[0].event_id if duplicate else uuid4(),
            )
            return Mutation(snapshot=target, events=[draft, status(target)], report=report)

        with pytest.raises(ConflictError):
            await repo.transact(session_id, lambda record: finish(record, duplicate=True))
        after_failure = await repo.get(session_id)
        assert after_failure.snapshot == before.snapshot
        assert after_failure.events == before.events
        assert after_failure.report is None
        result = await repo.transact(session_id, finish)
        assert result.record.snapshot.state == SessionState.COMPLETED
        assert result.record.report == report
        assert (
            result.record.events[-2].payload["snapshot"]["last_event_seq"]
            == result.record.snapshot.last_event_seq
        )
        await repo.dispose()
        reopened = TrainingRepository(path)
        durable = await reopened.get(session_id)
        assert durable.report == report
        assert durable.snapshot.report_state.value == "ready"
        await create(reopened)  # committing completion releases the active-owner slot.
        await reopened.dispose()

    asyncio.run(scenario())


def test_retrying_failed_review_cannot_take_another_active_sessions_slot(tmp_path):
    async def scenario():
        repo = TrainingRepository(tmp_path / "training.sqlite3")
        failed_id = await reporting_session(repo)
        await repo.transact(
            failed_id,
            lambda record: Mutation(
                snapshot=transition_snapshot(record.snapshot, SessionState.FAILED, report_state="failed")
            ),
        )
        active_id = (await create(repo)).record.snapshot.session_id
        with pytest.raises(BusyError) as error:
            await repo.transact(
                failed_id,
                lambda record: Mutation(snapshot=transition_snapshot(record.snapshot, SessionState.RUNNING)),
            )
        assert error.value.session_id == active_id
        assert (await repo.get(failed_id)).snapshot.state == SessionState.FAILED
        await repo.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("state", [SessionState.RUNNING, SessionState.CANCELLING])
def test_restart_recovery_marks_inflight_states_interrupted_once(tmp_path, state):
    async def scenario():
        path = tmp_path / "training.sqlite3"
        repo = TrainingRepository(path)
        session_id = (await create(repo)).record.snapshot.session_id
        await repo.transact(
            session_id,
            lambda record: Mutation(snapshot=transition_snapshot(record.snapshot, SessionState.RUNNING)),
        )
        if state == SessionState.CANCELLING:
            await repo.transact(
                session_id,
                lambda record: Mutation(
                    snapshot=transition_snapshot(record.snapshot, SessionState.CANCELLING)
                ),
            )
        before = await repo.get(session_id)
        await repo.dispose()
        reopened = TrainingRepository(path)
        assert (await reopened.get(session_id)).snapshot.state == state
        assert await reopened.recover_interrupted() == 1
        recovered = await reopened.get(session_id)
        assert recovered.snapshot.state == SessionState.INTERRUPTED
        assert recovered.snapshot.last_event_seq == before.snapshot.last_event_seq + 1
        assert await reopened.recover_interrupted() == 0
        await reopened.dispose()

    asyncio.run(scenario())


def test_cancellation_and_report_completion_cannot_both_commit(tmp_path):
    async def scenario():
        path = tmp_path / "training.sqlite3"
        left, right = TrainingRepository(path), TrainingRepository(path)
        session_id = await reporting_session(left)

        def complete(record):
            target = transition_snapshot(record.snapshot, SessionState.COMPLETED, report_state="ready")
            return Mutation(
                snapshot=target,
                events=[status(target)],
                report={"session_id": str(session_id), "content": "committed report"},
            )

        def cancel(record):
            target = transition_snapshot(record.snapshot, SessionState.CANCELLING, report_state="cancelled")
            return Mutation(snapshot=target, events=[status(target)])

        outcomes = await asyncio.gather(
            left.transact(session_id, complete), right.transact(session_id, cancel), return_exceptions=True
        )
        assert sum(not isinstance(item, Exception) for item in outcomes) == 1
        assert sum(isinstance(item, ValueError) for item in outcomes) == 1
        restored = await left.get(session_id)
        if restored.snapshot.state == SessionState.COMPLETED:
            assert restored.report is not None and restored.snapshot.report_state.value == "ready"
        else:
            assert restored.snapshot.state == SessionState.CANCELLING
            assert restored.report is None and restored.snapshot.report_state.value == "cancelled"
        await left.dispose()
        await right.dispose()

    asyncio.run(scenario())


def test_independent_process_reads_committed_session_and_resumable_waiting_state(tmp_path):
    path = tmp_path / "training.sqlite3"

    async def prepare():
        repo = TrainingRepository(path)
        session_id = (await create(repo)).record.snapshot.session_id
        await activate(repo, session_id)
        await repo.dispose()
        return str(session_id)

    session_id = asyncio.run(prepare())
    root = Path(__file__).resolve().parents[2]
    script = """
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from repo_bootstrap import bootstrap_local_packages
bootstrap_local_packages(Path(sys.argv[1]))
from healteam_memory.training_store import TrainingRepository
async def main():
    repo = TrainingRepository(sys.argv[2])
    assert await repo.recover_interrupted() == 0
    record = await repo.get(sys.argv[3])
    print(json.dumps({'state': record.snapshot.state.value, 'seq': record.snapshot.last_event_seq, 'case': record.case_snapshot['title']}))
    await repo.dispose()
asyncio.run(main())
"""
    env = dict(os.environ, HEALTEAM_ROOT=str(root), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(root), str(path), session_id],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"state": "awaiting_input", "seq": 2, "case": "Stored case"}


@pytest.mark.parametrize("value", [0, -1, 31, float("nan"), float("inf"), True, "1", None])
def test_busy_timeout_rejects_invalid_or_unbounded_values(tmp_path, value):
    with pytest.raises(ValueError, match="busy_timeout_seconds"):
        TrainingRepository(tmp_path / "training.sqlite3", busy_timeout_seconds=value)


def test_sqlite_writer_contention_times_out_without_running_or_repeating_callback(tmp_path):
    async def scenario():
        path = tmp_path / "training.sqlite3"
        repo = TrainingRepository(path, busy_timeout_seconds=0.05)
        before = (await create(repo)).record
        calls = 0

        def mutate(record):
            nonlocal calls
            calls += 1
            target = transition_snapshot(record.snapshot, SessionState.RUNNING)
            return Mutation(snapshot=target, events=[status(target)], receipt={"accepted": True})

        holder = sqlite3.connect(path)
        try:
            holder.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            with pytest.raises(OperationalError, match="locked"):
                await repo.transact(
                    before.snapshot.session_id,
                    mutate,
                    receipt_key="contended-request",
                    receipt_payload={"turn": 1},
                )
            assert time.monotonic() - started < 1.5
            assert calls == 0
            assert await repo.get(before.snapshot.session_id) == before
        finally:
            holder.rollback()
            holder.close()
        accepted = await repo.transact(
            before.snapshot.session_id,
            mutate,
            receipt_key="contended-request",
            receipt_payload={"turn": 1},
        )
        replayed = await repo.transact(
            before.snapshot.session_id,
            mutate,
            receipt_key="contended-request",
            receipt_payload={"turn": 1},
        )
        assert not accepted.replayed and replayed.replayed
        assert calls == 1
        assert replayed.record.snapshot.last_event_seq == before.snapshot.last_event_seq + 1
        assert accepted.record == replayed.record
        await repo.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["supervision", "report"])
def test_interrupted_review_can_retry_saved_phase_without_taking_another_active_slot(tmp_path, phase):
    async def scenario():
        repo = TrainingRepository(tmp_path / "training.sqlite3")
        session_id = (await create(repo)).record.snapshot.session_id

        def start(record):
            target = transition_snapshot(record.snapshot, SessionState.RUNNING).model_copy(
                update={"current_round": 1}
            )
            return Mutation(snapshot=target, events=[status(target)])

        await repo.transact(session_id, start)
        await repo.transact(
            session_id,
            lambda record: Mutation(
                snapshot=transition_snapshot(record.snapshot, SessionState.RUNNING, phase="supervision"),
                completed_rounds=1,
            ),
        )
        if phase == "report":
            await repo.transact(
                session_id,
                lambda record: Mutation(
                    snapshot=transition_snapshot(
                        record.snapshot, SessionState.RUNNING, phase="report", report_state="generating"
                    )
                ),
            )
        assert await repo.recover_interrupted() == 1
        interrupted = await repo.get(session_id)
        assert interrupted.snapshot.state == SessionState.INTERRUPTED
        assert interrupted.snapshot.phase == phase
        assert interrupted.snapshot.report_state.value == "failed"
        assert interrupted.completed_rounds == 1

        def retry(record):
            target = transition_snapshot(
                record.snapshot,
                SessionState.RUNNING,
                report_state="generating" if phase == "report" else "failed",
            )
            return Mutation(snapshot=target, events=[status(target)])

        active_id = (await create(repo)).record.snapshot.session_id
        with pytest.raises(BusyError) as error:
            await repo.transact(session_id, retry)
        assert error.value.session_id == active_id
        assert await repo.get(session_id) == interrupted
        await repo.transact(
            active_id,
            lambda record: Mutation(snapshot=transition_snapshot(record.snapshot, SessionState.CANCELLING)),
        )
        await repo.transact(
            active_id,
            lambda record: Mutation(snapshot=transition_snapshot(record.snapshot, SessionState.CANCELLED)),
        )
        retried = (await repo.transact(session_id, retry)).record
        assert retried.snapshot.state == SessionState.RUNNING
        assert retried.snapshot.phase == phase
        assert retried.completed_rounds == interrupted.completed_rounds
        assert retried.events[:-1] == interrupted.events
        await repo.dispose()

    asyncio.run(scenario())


def test_interrupted_simulation_cannot_resume_or_append_events(tmp_path):
    async def scenario():
        repo = TrainingRepository(tmp_path / "training.sqlite3")
        session_id = (await create(repo)).record.snapshot.session_id
        await repo.recover_interrupted()
        before = await repo.get(session_id)
        with pytest.raises(ValueError, match="terminal"):
            await repo.transact(
                session_id,
                lambda record: Mutation(
                    snapshot=record.snapshot.model_copy(update={"state": SessionState.RUNNING})
                ),
            )
        with pytest.raises(ValueError, match="terminal"):
            await repo.transact(
                session_id,
                lambda record: Mutation(snapshot=record.snapshot, events=[status(record.snapshot)]),
            )
        assert await repo.get(session_id) == before
        await repo.dispose()

    asyncio.run(scenario())


def test_recovery_keeps_cancelled_review_cancelled_and_not_retryable(tmp_path):
    async def scenario():
        repo = TrainingRepository(tmp_path / "training.sqlite3")
        session_id = await reporting_session(repo)
        await repo.transact(
            session_id,
            lambda record: Mutation(
                snapshot=transition_snapshot(
                    record.snapshot, SessionState.CANCELLING, report_state="cancelled"
                )
            ),
        )
        assert await repo.recover_interrupted() == 1
        interrupted = await repo.get(session_id)
        assert interrupted.snapshot.report_state.value == "cancelled"
        with pytest.raises(ValueError, match="terminal"):
            await repo.transact(
                session_id,
                lambda record: Mutation(
                    snapshot=transition_snapshot(
                        record.snapshot, SessionState.RUNNING, report_state="generating"
                    )
                ),
            )
        assert await repo.get(session_id) == interrupted
        await repo.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("state", [SessionState.FAILED, SessionState.INTERRUPTED])
@pytest.mark.parametrize("change", ["round", "utterance"])
def test_review_retry_cannot_modify_saved_dialogue(tmp_path, state, change):
    async def scenario():
        repo = TrainingRepository(tmp_path / "training.sqlite3")
        session_id = await reporting_session(repo)
        if state == SessionState.INTERRUPTED:
            await repo.recover_interrupted()
        else:
            await repo.transact(
                session_id,
                lambda record: Mutation(
                    snapshot=transition_snapshot(record.snapshot, state, report_state="failed")
                ),
            )
        before = await repo.get(session_id)

        def retry(record):
            target = transition_snapshot(record.snapshot, SessionState.RUNNING, report_state="generating")
            if change == "round":
                target = target.model_copy(update={"current_round": target.current_round + 1})
                drafts = [status(target)]
            else:
                drafts = [
                    EventDraft(
                        round=1,
                        type="utterance",
                        actor="father",
                        source="ai",
                        visibility="public",
                        payload={"content": "This must not be regenerated during a report retry."},
                    )
                ]
            return Mutation(snapshot=target, events=drafts)

        with pytest.raises(ValueError, match="review retry cannot change"):
            await repo.transact(session_id, retry)
        assert await repo.get(session_id) == before
        await repo.dispose()

    asyncio.run(scenario())
