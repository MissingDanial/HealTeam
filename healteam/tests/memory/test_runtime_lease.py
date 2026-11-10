"""Real process ownership and crash checks; all files are temporary."""

from __future__ import annotations

import asyncio
import gc
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from healteam_memory.contracts import CreateSessionCommand, SessionState, transition_snapshot
from healteam_memory.runtime_lease import RuntimeInUseError, RuntimeLease
from healteam_memory.training_store import EventDraft, Mutation, TrainingRepository

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from repo_bootstrap import bootstrap_local_packages
bootstrap_local_packages(Path(sys.argv[1]))
"""


def child_command(script, *arguments):
    return [sys.executable, "-B", "-c", BOOTSTRAP + script, str(ROOT), *(str(a) for a in arguments)]


def child_environment():
    return dict(os.environ, HEALTEAM_ROOT=str(ROOT), PYTHONDONTWRITEBYTECODE="1")


def test_same_process_handles_compete_and_own_object_is_idempotent(tmp_path):
    path = tmp_path / "folder" / "training.sqlite3"
    owner = RuntimeLease(path)
    contender = RuntimeLease(path.parent / ".." / "folder" / path.name)
    owner.acquire()
    owner.acquire()
    assert owner.acquired
    assert not path.exists()  # Ownership never initializes or rewrites the DB.
    with pytest.raises(RuntimeInUseError) as error:
        contender.acquire()
    assert error.value.code == "runtime_in_use"
    assert error.value.status_code == 503
    assert not contender.acquired
    owner.release()
    owner.release()
    assert owner.path.exists()  # Do not unlink a possibly contested inode.
    contender.acquire()
    assert contender.acquired
    contender.release()


def test_context_exception_releases_and_unreferenced_owner_does_not_leak(tmp_path):
    path = tmp_path / "training.sqlite3"
    with pytest.raises(ValueError), RuntimeLease(path):
        raise ValueError("test failure")
    owner = RuntimeLease(path)
    owner.acquire()
    del owner
    gc.collect()
    with RuntimeLease(path) as replacement:
        assert replacement.acquired


def test_independent_live_owner_cannot_be_taken_over_by_old_sidecar_timestamp(tmp_path):
    path, ready = tmp_path / "training.sqlite3", tmp_path / "ready"
    script = """
from healteam_memory.runtime_lease import RuntimeLease
with RuntimeLease(sys.argv[2]):
    Path(sys.argv[3]).write_text('ready', encoding='ascii')
    sys.stdin.readline()
"""
    process = subprocess.Popen(
        child_command(script, path, ready),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=child_environment(),
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "child failed to acquire its test lock"
        contender = RuntimeLease(path)
        os.utime(contender.path, (0, 0))
        with pytest.raises(RuntimeInUseError):
            contender.acquire()
        assert not path.exists()
        stdout, stderr = process.communicate("stop\n", timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        contender.acquire()
        contender.release()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_forced_process_termination_releases_ownership(tmp_path):
    path, ready = tmp_path / "training.sqlite3", tmp_path / "ready"
    script = """
from healteam_memory.runtime_lease import RuntimeLease
lease = RuntimeLease(sys.argv[2])
lease.acquire()
Path(sys.argv[3]).write_text('ready', encoding='ascii')
sys.stdin.readline()
"""
    process = subprocess.Popen(
        child_command(script, path, ready),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_environment(),
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        process.kill()
        process.communicate(timeout=10)
        with RuntimeLease(path) as replacement:
            assert replacement.acquired
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_competing_process_cannot_recover_the_live_owners_session(tmp_path):
    path = tmp_path / "training.sqlite3"

    async def prepare():
        repo = TrainingRepository(path)
        created = await repo.create(
            CreateSessionCommand(client_request_id=uuid4(), case_id="teen-truancy-01", school="satir"),
            config_snapshot={"model_mode": "mock"},
            case_snapshot={"case_id": "teen-truancy-01", "version": "1.0.0"},
        )
        record = (
            await repo.transact(
                created.record.snapshot.session_id,
                lambda row: Mutation(snapshot=transition_snapshot(row.snapshot, SessionState.RUNNING)),
            )
        ).record
        await repo.dispose()
        return record

    before = asyncio.run(prepare())
    script = """
import asyncio
from healteam_memory.runtime_lease import RuntimeInUseError, RuntimeLease
from healteam_memory.training_store import TrainingRepository
async def main():
    try:
        with RuntimeLease(sys.argv[2]):
            repository = TrainingRepository(sys.argv[2])
            await repository.recover_interrupted()
            await repository.dispose()
    except RuntimeInUseError as exc:
        print(exc.code)
asyncio.run(main())
"""
    with RuntimeLease(path):
        result = subprocess.run(
            child_command(script, path), capture_output=True, text=True, env=child_environment(), timeout=20
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "runtime_in_use"

        async def verify():
            repo = TrainingRepository(path)
            after = await repo.get(before.snapshot.session_id)
            await repo.dispose()
            return after

        assert asyncio.run(verify()) == before


def test_process_exit_before_commit_rolls_back_snapshot_events_and_receipt(tmp_path):
    path = tmp_path / "training.sqlite3"

    async def prepare():
        repo = TrainingRepository(path)
        first = await repo.create(
            CreateSessionCommand(client_request_id=uuid4(), case_id="teen-truancy-01", school="satir"),
            config_snapshot={"model_mode": "mock"},
            case_snapshot={"case_id": "teen-truancy-01", "version": "1.0.0"},
        )
        await repo.dispose()
        return first.record

    before = asyncio.run(prepare())
    script = """
import asyncio, os
from sqlalchemy.ext.asyncio import AsyncSession
from healteam_memory.contracts import SessionState, transition_snapshot
from healteam_memory.runtime_lease import RuntimeLease
from healteam_memory.training_store import EventDraft, Mutation, TrainingRepository
async def main():
    lease = RuntimeLease(sys.argv[2])
    lease.acquire()
    repository = TrainingRepository(sys.argv[2])
    await repository.initialize()
    async def crash_before_commit(_session):
        os._exit(23)
    AsyncSession.commit = crash_before_commit
    def mutate(record):
        target = transition_snapshot(record.snapshot, SessionState.RUNNING)
        return Mutation(snapshot=target, events=[EventDraft(round=0, type='status_changed', actor='system', source='system', visibility='public', payload={'reason':'crash_probe'})], receipt={'accepted':True})
    await repository.transact(sys.argv[3], mutate, receipt_key='crash-probe', receipt_payload={'turn':1})
asyncio.run(main())
"""
    result = subprocess.run(
        child_command(script, path, before.snapshot.session_id),
        capture_output=True,
        text=True,
        env=child_environment(),
        timeout=20,
    )
    assert result.returncode == 23, result.stderr

    async def verify():
        with RuntimeLease(path):
            repo = TrainingRepository(path)
            assert await repo.get(before.snapshot.session_id) == before

            def mutate(record):
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
                            payload={"reason": "retry_probe"},
                        )
                    ],
                    receipt={"accepted": True},
                )

            accepted = await repo.transact(
                before.snapshot.session_id, mutate, receipt_key="crash-probe", receipt_payload={"turn": 1}
            )
            assert not accepted.replayed
            replayed = await repo.transact(
                before.snapshot.session_id, mutate, receipt_key="crash-probe", receipt_payload={"turn": 1}
            )
            assert replayed.replayed
            assert accepted.record.snapshot.last_event_seq == before.snapshot.last_event_seq + 1
            assert replayed.record.events == accepted.record.events
            await repo.dispose()

    asyncio.run(verify())


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork ownership check")
def test_forked_child_release_does_not_unlock_parent(tmp_path):
    path = tmp_path / "training.sqlite3"
    owner = RuntimeLease(path)
    with owner:
        reader, writer = os.pipe()
        child_pid = os.fork()
        if child_pid == 0:
            os.close(reader)
            owner.release()
            try:
                RuntimeLease(path).acquire()
            except RuntimeInUseError:
                os.write(writer, b"blocked")
            finally:
                os.close(writer)
                os._exit(0)
        os.close(writer)
        try:
            assert os.read(reader, 16) == b"blocked"
            _, status = os.waitpid(child_pid, 0)
            assert status == 0
            assert owner.acquired
            with pytest.raises(RuntimeInUseError):
                RuntimeLease(path).acquire()
        finally:
            os.close(reader)
