"""Fault injection for deadlines, ignored cancellation, and stale workers."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from app.services.training_manager import ManagerError, TrainingManager
from app.settings import Settings
from healteam_memory.contracts import CreateSessionCommand, SubmitMessageCommand
from healteam_memory.runtime_lease import RuntimeLease
from healteam_memory.training_store import TrainingRepository


def command(**updates):
    return CreateSessionCommand.model_validate(
        {"client_request_id": str(uuid4()), "case_id": "teen-truancy-01", "school": "structural", **updates}
    )


def message():
    return SubmitMessageCommand(client_message_id=uuid4(), expected_turn=1, content="请爸爸说说")


async def state(manager, session_id, expected):
    async with asyncio.timeout(4):
        while True:
            result = await manager.snapshot(session_id)
            if result["state"] == expected:
                return result
            await asyncio.sleep(0.005)


@pytest.fixture
async def manager(tmp_path):
    repository = TrainingRepository(tmp_path / "reliability.sqlite3")
    service = TrainingManager(
        repository,
        settings=Settings(),
        response_delay=0.001,
        operation_timeout_seconds=0.05,
        stop_timeout_seconds=0.02,
    )
    await service.startup()
    yield service
    await service.shutdown()
    await repository.dispose()


async def test_response_timeout_preserves_input_and_releases_active_slot(manager, monkeypatch):
    async def stalled(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(manager.family, "respond", stalled)
    view, _ = await manager.create(command())
    accepted = message()
    receipt = await manager.submit(view["session_id"], accepted)
    failed = await state(manager, view["session_id"], "failed")
    assert failed["error_code"] == "response_timeout"
    assert failed["completed_rounds"] == 0
    assert [e["actor"] for e in failed["events"] if e["type"] == "utterance"] == ["trainee"]
    assert await manager.submit(view["session_id"], accepted) == receipt
    later, _ = await manager.create(command())
    assert later["state"] == "awaiting_input"
    await manager.cancel(later["session_id"])


async def test_timeout_discards_provider_that_ignores_cancellation(manager, monkeypatch):
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def stubborn(*args, **kwargs):
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return "迟到的家庭回应，不应保存"

    monkeypatch.setattr(manager.family, "respond", stubborn)
    view, _ = await manager.create(command())
    try:
        await manager.submit(view["session_id"], message())
        async with asyncio.timeout(0.8):
            failed = await state(manager, view["session_id"], "failed")
            await cancelled.wait()
        assert failed["error_code"] == "response_timeout"
        tasks = tuple(manager._generation_tasks)
        release.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        assert await manager.snapshot(view["session_id"]) == failed
    finally:
        release.set()


async def test_late_report_cannot_overwrite_successful_retry(manager, monkeypatch):
    release = asyncio.Event()
    cancelled = asyncio.Event()
    original = manager._generate_report

    async def stubborn(record):
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        result = await original(record)
        result["content"] = "旧任务迟到的报告"
        return result

    monkeypatch.setattr(manager, "_generate_report", stubborn)
    view, _ = await manager.create(command(max_rounds=1))
    try:
        await manager.submit(view["session_id"], message())
        failed = await state(manager, view["session_id"], "failed")
        await cancelled.wait()
        assert failed["error_code"] == "report_timeout" and failed["report_state"] == "failed"
        utterances = [e for e in failed["events"] if e["type"] == "utterance"]
        monkeypatch.setattr(manager, "_generate_report", original)
        retry = await manager.retry_report(view["session_id"])
        assert retry["error_code"] is None
        completed = await state(manager, view["session_id"], "completed")
        saved = await manager.report(view["session_id"])
        tasks = tuple(manager._generation_tasks)
        release.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        assert await manager.snapshot(view["session_id"]) == completed
        assert await manager.report(view["session_id"]) == saved
        assert [e for e in completed["events"] if e["type"] == "utterance"] == utterances
    finally:
        release.set()


async def test_cancel_returns_without_waiting_for_stubborn_generation(manager, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def stubborn(*args, **kwargs):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return "取消后迟到"

    monkeypatch.setattr(manager.family, "respond", stubborn)
    view, _ = await manager.create(command())
    try:
        await manager.submit(view["session_id"], message())
        await entered.wait()
        async with asyncio.timeout(0.8):
            cancelled = await manager.cancel(view["session_id"])
        assert cancelled["state"] == "cancelled"
        later, _ = await manager.create(command())
        assert later["state"] == "awaiting_input"
        tasks = tuple(manager._generation_tasks)
        release.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        assert await manager.snapshot(view["session_id"]) == cancelled
        await manager.cancel(later["session_id"])
    finally:
        release.set()


async def test_shutdown_returns_and_late_generation_cannot_change_interruption(manager, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def stubborn(*args, **kwargs):
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
        return "停机后迟到"

    monkeypatch.setattr(manager.family, "respond", stubborn)
    view, _ = await manager.create(command())
    try:
        await manager.submit(view["session_id"], message())
        await entered.wait()
        async with asyncio.timeout(0.8):
            await manager.shutdown()
        interrupted = await manager.snapshot(view["session_id"])
        assert interrupted["state"] == "interrupted"
        await manager.startup()
        later, _ = await manager.create(command())
        tasks = tuple(manager._generation_tasks)
        release.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        assert await manager.snapshot(view["session_id"]) == interrupted
        await manager.cancel(later["session_id"])
    finally:
        release.set()


async def test_revoked_worker_cannot_commit_delayed_transaction(manager, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    original = manager.repository.transact

    async def delayed(*args, **kwargs):
        task = asyncio.current_task()
        if task.get_name().startswith("training:") and not entered.is_set():
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
        return await original(*args, **kwargs)

    monkeypatch.setattr(manager.repository, "transact", delayed)
    view, _ = await manager.create(command())
    try:
        await manager.submit(view["session_id"], message())
        await entered.wait()
        old_worker = manager._tasks[view["session_id"]]
        async with asyncio.timeout(0.8):
            cancelled = await manager.cancel(view["session_id"])
        assert cancelled["state"] == "cancelled"
        release.set()
        await asyncio.gather(old_worker, return_exceptions=True)
        assert await manager.snapshot(view["session_id"]) == cancelled
        assert [e["actor"] for e in cancelled["events"] if e["type"] == "utterance"] == ["trainee"]
    finally:
        release.set()


async def test_interrupted_report_can_retry_without_repeating_utterances(manager, monkeypatch):
    entered = asyncio.Event()
    original = manager._generate_report

    async def paused(record):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_generate_report", paused)
    view, _ = await manager.create(command(max_rounds=1))
    await manager.submit(view["session_id"], message())
    await entered.wait()
    await manager.shutdown()
    interrupted = await manager.snapshot(view["session_id"])
    assert interrupted["report_state"] == "failed"
    utterances = [e for e in interrupted["events"] if e["type"] == "utterance"]
    monkeypatch.setattr(manager, "_generate_report", original)
    await manager.startup()
    retry = await manager.retry_report(view["session_id"])
    assert retry["error_code"] is None
    completed = await state(manager, view["session_id"], "completed")
    assert [e for e in completed["events"] if e["type"] == "utterance"] == utterances


async def test_interrupted_simulation_cannot_retry_as_report(manager):
    manager.response_delay = 0.1
    view, _ = await manager.create(command())
    await manager.submit(view["session_id"], message())
    await manager.shutdown()
    await manager.startup()
    with pytest.raises(ManagerError) as failure:
        await manager.retry_report(view["session_id"])
    assert failure.value.code == "report_retry_unavailable"


async def test_supervision_timeout_can_retry_saved_review_phase(manager, monkeypatch):
    async def immediate(*args, **kwargs):
        return "离线家庭回应"

    monkeypatch.setattr(manager.family, "respond", immediate)
    manager.response_delay = 0.1
    view, _ = await manager.create(command(max_rounds=1))
    await manager.submit(view["session_id"], message())
    failed = await state(manager, view["session_id"], "failed")
    assert failed["phase"] == "supervision"
    assert failed["error_code"] == "report_timeout" and failed["report_state"] == "failed"
    manager.response_delay = 0.001
    await manager.retry_report(view["session_id"])
    completed = await state(manager, view["session_id"], "completed")
    assert completed["completed_rounds"] == 1


@pytest.mark.parametrize("restart", [False, True])
async def test_command_delayed_before_transaction_cannot_activate_after_shutdown(
    manager, monkeypatch, restart
):
    entered = asyncio.Event()
    release = asyncio.Event()
    original = manager.repository.transact

    async def delayed(*args, **kwargs):
        if args[1].__name__ == "accept":
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    view, _ = await manager.create(command())
    monkeypatch.setattr(manager.repository, "transact", delayed)
    pending = asyncio.create_task(manager.submit(view["session_id"], message()))
    try:
        await entered.wait()
        await manager.shutdown()
        before = await manager.snapshot(view["session_id"])
        if restart:
            await manager.startup()
        release.set()
        with pytest.raises(ManagerError) as failure:
            await pending
        assert failure.value.code == "service_not_ready"
        assert await manager.snapshot(view["session_id"]) == before
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_late_create_from_old_owner_cannot_write_after_new_runtime_starts(tmp_path, monkeypatch):
    database = tmp_path / "ownership.sqlite3"
    old_lease = RuntimeLease(database)
    old_lease.acquire()
    repository = TrainingRepository(database, write_guard=lambda: old_lease.acquired)
    old = TrainingManager(repository, settings=Settings(), response_delay=0.001)
    await old.startup()
    entered = asyncio.Event()
    release = asyncio.Event()
    original = repository.create

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(repository, "create", delayed)
    pending = asyncio.create_task(old.create(command()))
    next_lease = RuntimeLease(database)
    next_repository = TrainingRepository(database, write_guard=lambda: next_lease.acquired)
    next_manager = TrainingManager(next_repository, settings=Settings(), response_delay=0.001)
    try:
        await entered.wait()
        await old.shutdown()
        old_lease.release()
        next_lease.acquire()
        await next_manager.startup()
        release.set()
        with pytest.raises(ManagerError) as failure:
            await pending
        assert failure.value.code == "runtime_not_owned" and failure.value.status_code == 503
        assert (await next_manager.list_sessions())["total"] == 0
        assert not old._tasks
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await old.shutdown()
        await repository.dispose()
        old_lease.release()
        if next_lease.acquired:
            await next_manager.shutdown()
        await next_repository.dispose()
        next_lease.release()


async def test_deadline_resets_for_each_response_not_entire_demo(manager):
    manager.response_delay = 0.03
    view, _ = await manager.create(command(mode="demo", max_rounds=2))
    completed = await state(manager, view["session_id"], "completed")
    assert completed["completed_rounds"] == 2


def test_manager_uses_runtime_timeout_settings_by_default(tmp_path):
    repository = TrainingRepository(tmp_path / "settings.sqlite3")
    service = TrainingManager(
        repository,
        settings=Settings(timeout_seconds=1, operation_timeout_seconds=0.3, stop_timeout_seconds=0.04),
    )
    assert service.operation_timeout_seconds == 0.3
    assert service.stop_timeout_seconds == 0.04


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
@pytest.mark.parametrize("option", ["operation_timeout_seconds", "stop_timeout_seconds"])
def test_invalid_deadline_cannot_disable_timeout(tmp_path, value, option):
    repository = TrainingRepository(tmp_path / "invalid.sqlite3")
    with pytest.raises(ValueError, match="finite and positive"):
        TrainingManager(repository, settings=Settings(), **{option: value})
