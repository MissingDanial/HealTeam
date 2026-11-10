"""An old runtime cannot commit after losing database execution ownership."""

from __future__ import annotations

import asyncio
import sqlite3
from uuid import uuid4

import pytest
from healteam_memory.contracts import CreateSessionCommand, SessionState, transition_snapshot
from healteam_memory.training_store import (
    EventDraft,
    Mutation,
    RuntimeNotOwnedError,
    TrainingRepository,
)
from sqlalchemy import event


async def create(repo):
    return await repo.create(
        CreateSessionCommand(client_request_id=uuid4(), case_id="teen-truancy-01", school="satir"),
        config_snapshot={"model_mode": "mock"},
        case_snapshot={"case_id": "teen-truancy-01", "version": "1.0.0"},
    )


def start(record):
    target = transition_snapshot(record.snapshot, SessionState.RUNNING)
    return Mutation(
        snapshot=target,
        events=[
            EventDraft(
                round=0,
                type="status_changed",
                actor="system",
                source="system",
                visibility="public",
                payload={"reason": "accepted"},
            )
        ],
        receipt={"accepted": True},
    )


def test_unowned_initialize_does_not_create_or_version_training_tables(tmp_path):
    async def scenario():
        path = tmp_path / "training.sqlite3"
        owned = False
        repo = TrainingRepository(path, write_guard=lambda: owned)
        with pytest.raises(RuntimeNotOwnedError) as error:
            await repo.initialize()
        assert error.value.code == "runtime_not_owned"
        assert error.value.status_code == 503
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
            assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        owned = True
        await repo.initialize()
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        await repo.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["create", "transact", "recover"])
def test_unowned_writer_rejected_while_existing_record_remains_readable(tmp_path, operation):
    async def scenario():
        owned = True
        repo = TrainingRepository(tmp_path / "training.sqlite3", write_guard=lambda: owned)
        before = (await create(repo)).record
        owned = False
        with pytest.raises(RuntimeNotOwnedError):
            if operation == "create":
                await create(repo)
            elif operation == "transact":
                await repo.transact(before.snapshot.session_id, start)
            else:
                await repo.recover_interrupted()
        assert await repo.get(before.snapshot.session_id) == before
        assert (await repo.list_sessions())[1] == 1
        await repo.dispose()

    asyncio.run(scenario())


def test_writer_losing_ownership_while_waiting_for_sqlite_lock_does_not_run_callback(tmp_path):
    async def scenario():
        path = tmp_path / "training.sqlite3"
        owned = True
        repo = TrainingRepository(path, write_guard=lambda: owned)
        before = (await create(repo)).record
        waiting = asyncio.Event()
        called = False

        def before_execute(_conn, _cursor, statement, *_args):
            if statement == "BEGIN IMMEDIATE":
                waiting.set()

        def mutate(record):
            nonlocal called
            called = True
            return start(record)

        event.listen(repo.engine.sync_engine, "before_cursor_execute", before_execute)
        holder = sqlite3.connect(path)
        task = None
        try:
            holder.execute("BEGIN IMMEDIATE")
            task = asyncio.create_task(
                repo.transact(
                    before.snapshot.session_id,
                    mutate,
                    receipt_key="late-command",
                    receipt_payload={"turn": 1},
                )
            )
            await asyncio.wait_for(waiting.wait(), timeout=2)
            owned = False
            holder.rollback()
            with pytest.raises(RuntimeNotOwnedError):
                await task
            assert not called
            assert await repo.get(before.snapshot.session_id) == before
            owned = True
            accepted = await repo.transact(
                before.snapshot.session_id,
                mutate,
                receipt_key="late-command",
                receipt_payload={"turn": 1},
            )
            assert not accepted.replayed
            assert accepted.record.snapshot.last_event_seq == before.snapshot.last_event_seq + 1
        finally:
            holder.rollback()
            holder.close()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            event.remove(repo.engine.sync_engine, "before_cursor_execute", before_execute)
            await repo.dispose()

    asyncio.run(scenario())


def test_ownership_lost_after_event_flush_rolls_back_snapshot_events_and_receipt(tmp_path, monkeypatch):
    async def scenario():
        owned = True
        repo = TrainingRepository(tmp_path / "training.sqlite3", write_guard=lambda: owned)
        before = (await create(repo)).record
        original = repo._apply

        async def revoke_after_apply(*args):
            nonlocal owned
            result = await original(*args)
            owned = False
            return result

        monkeypatch.setattr(repo, "_apply", revoke_after_apply)
        with pytest.raises(RuntimeNotOwnedError):
            await repo.transact(
                before.snapshot.session_id,
                start,
                receipt_key="late-command",
                receipt_payload={"turn": 1},
            )
        assert await repo.get(before.snapshot.session_id) == before
        owned = True
        monkeypatch.setattr(repo, "_apply", original)
        accepted = await repo.transact(
            before.snapshot.session_id,
            start,
            receipt_key="late-command",
            receipt_payload={"turn": 1},
        )
        assert not accepted.replayed
        replayed = await repo.transact(
            before.snapshot.session_id,
            start,
            receipt_key="late-command",
            receipt_payload={"turn": 1},
        )
        assert replayed.replayed
        assert accepted.record.events == replayed.record.events
        assert accepted.record.snapshot.last_event_seq == before.snapshot.last_event_seq + 1
        await repo.dispose()

    asyncio.run(scenario())
