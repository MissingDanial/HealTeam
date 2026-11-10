from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from app.services.training_manager import ManagerError, TrainingManager
from app.settings import Settings
from healteam_memory.contracts import CreateSessionCommand, SubmitMessageCommand
from healteam_memory.training_store import TrainingRepository


def create_command(**updates):
    return CreateSessionCommand.model_validate(
        {"client_request_id": str(uuid4()), "case_id": "teen-truancy-01", "school": "structural", **updates}
    )


def message(content="请爸爸说说你刚才的想法", turn=1, message_id=None):
    return SubmitMessageCommand(client_message_id=message_id or uuid4(), expected_turn=turn, content=content)


async def wait_state(manager, session_id, *states):
    async with asyncio.timeout(5):
        while True:
            view = await manager.snapshot(session_id)
            if view["state"] in states:
                return view
            await asyncio.sleep(0.01)


@pytest.fixture
async def manager(tmp_path):
    repository = TrainingRepository(tmp_path / "training.sqlite3")
    service = TrainingManager(repository, settings=Settings(), response_delay=0.001)
    await service.startup()
    yield service
    await service.shutdown()
    await repository.dispose()


async def test_create_waits_for_human_and_replays_create_receipt(manager):
    command = create_command()
    first, created = await manager.create(command)
    again, created_again = await manager.create(command)
    assert created and not created_again
    assert first["session_id"] == again["session_id"]
    assert first["state"] == "awaiting_input"
    assert first["current_round"] == first["completed_rounds"] == 0
    assert first["model_mode"] == "mock"
    assert not [event for event in first["events"] if event["type"] == "utterance"]
    assert (await manager.report(first["session_id"]))["status"] == "not_started"


async def test_single_active_session_conflict_contains_reopen_id(manager):
    first, _ = await manager.create(create_command())
    with pytest.raises(ManagerError) as failure:
        await manager.create(create_command())
    assert failure.value.status_code == 409
    assert failure.value.code == "active_session_exists"
    assert str(failure.value.details["session_id"]) == first["session_id"]


async def test_training_round_has_human_and_family_without_ai_doctor(manager):
    view, _ = await manager.create(create_command(max_rounds=2))
    receipt = await manager.submit(view["session_id"], message("请爸爸说说你刚才的想法"))
    assert receipt["accepted_turn"] == 1
    view = await wait_state(manager, view["session_id"], "awaiting_input")
    utterances = [event for event in view["events"] if event["type"] == "utterance"]
    assert utterances[0]["actor"] == "trainee" and utterances[0]["source"] == "human"
    family = [event for event in utterances if event["source"] == "ai"]
    assert 1 <= len(family) <= 3
    assert {event["actor"] for event in family} <= {"father", "mother", "patient"}
    assert "刚才的想法" in family[0]["payload"]["content"]
    assert view["completed_rounds"] == view["current_round"] == 1
    assert all(event["round"] == 1 for event in utterances)


async def test_message_replay_precedes_busy_and_completed_checks(manager):
    manager.response_delay = 0.02
    view, _ = await manager.create(create_command(max_rounds=1))
    command = message()
    first = await manager.submit(view["session_id"], command)
    assert await manager.submit(view["session_id"], command) == first
    await wait_state(manager, view["session_id"], "completed")
    assert await manager.submit(view["session_id"], command) == first
    view = await manager.snapshot(view["session_id"])
    assert len([event for event in view["events"] if event["actor"] == "trainee"]) == 1


async def test_new_busy_stale_and_changed_messages_conflict(manager):
    manager.response_delay = 0.1
    view, _ = await manager.create(create_command(max_rounds=2))
    command = message()
    await manager.submit(view["session_id"], command)
    for invalid in (message("换一段内容", message_id=command.client_message_id), message("另一条消息")):
        with pytest.raises(ManagerError) as failure:
            await manager.submit(view["session_id"], invalid)
        assert failure.value.status_code == 409
    await wait_state(manager, view["session_id"], "awaiting_input")
    with pytest.raises(ManagerError) as stale:
        await manager.submit(view["session_id"], message("旧轮号", turn=1))
    assert stale.value.code == "turn_conflict"


async def test_round_limit_commits_report_and_completed_together(manager):
    view, _ = await manager.create(create_command(max_rounds=1))
    await manager.submit(view["session_id"], message())
    completed = await wait_state(manager, view["session_id"], "completed")
    assert completed["phase"] == "report" and completed["report_state"] == "ready"
    report = await manager.report(view["session_id"])
    assert report["kind"] == "workflow_check" and report["status"] == "ready"
    assert "固定模板" in report["content"] and "不代表" in report["content"]
    assert "专业评分" in report["content"]
    assert "事件 #" in report["content"]
    ready_events = [event for event in completed["events"] if event["type"] == "report_ready"]
    assert len(ready_events) == 1
    assert ready_events[0]["payload"]["snapshot"]["state"] == "completed"


async def test_finish_requires_completed_round_and_does_not_generate_doctor(manager):
    view, _ = await manager.create(create_command(max_rounds=3))
    with pytest.raises(ManagerError) as failure:
        await manager.finish(view["session_id"])
    assert failure.value.code == "round_required"
    await manager.submit(view["session_id"], message())
    await wait_state(manager, view["session_id"], "awaiting_input")
    await manager.finish(view["session_id"])
    completed = await wait_state(manager, view["session_id"], "completed")
    assert completed["completed_rounds"] == 1
    assert not any(event["actor"] == "doctor" for event in completed["events"])


async def test_cancel_awaiting_session_is_idempotent(manager):
    view, _ = await manager.create(create_command())
    first = await manager.cancel(view["session_id"])
    again = await manager.cancel(view["session_id"])
    assert first == again
    assert first["state"] == "cancelled"
    assert not [event for event in first["events"] if event["type"] == "utterance"]
    assert (await manager.report(view["session_id"]))["status"] == "cancelled"


async def test_cancel_inflight_freezes_generation_but_replays_message_receipt(manager):
    manager.response_delay = 0.2
    view, _ = await manager.create(create_command(max_rounds=2))
    command = message("请爸爸妈妈和孩子分别说说")
    receipt = await manager.submit(view["session_id"], command)
    cancelled = await manager.cancel(view["session_id"])
    assert cancelled["state"] == "cancelled"
    await asyncio.sleep(0.25)
    assert await manager.snapshot(view["session_id"]) == cancelled
    assert await manager.submit(view["session_id"], command) == receipt
    with pytest.raises(ManagerError) as failure:
        await manager.submit(view["session_id"], message("新消息", turn=2))
    assert failure.value.status_code == 409


async def test_shutdown_interruption_never_autoruns_and_waiting_survives(tmp_path):
    database = tmp_path / "restart.sqlite3"
    repository = TrainingRepository(database)
    first = TrainingManager(repository, settings=Settings(), response_delay=0.2)
    await first.startup()
    running, _ = await first.create(create_command())
    await first.submit(running["session_id"], message())
    await first.shutdown()
    interrupted = await first.snapshot(running["session_id"])
    assert interrupted["state"] == "interrupted"
    await repository.dispose()
    second_repository = TrainingRepository(database)
    second = TrainingManager(second_repository, settings=Settings(), response_delay=0.001)
    await second.startup()
    try:
        assert await second.snapshot(running["session_id"]) == interrupted
        waiting, _ = await second.create(create_command())
        await second.shutdown()
        assert (await second.snapshot(waiting["session_id"]))["state"] == "awaiting_input"
    finally:
        await second.shutdown()
        await second_repository.dispose()


async def test_mock_demo_is_automatic_and_not_a_learner_score(manager):
    view, _ = await manager.create(create_command(mode="demo", max_rounds=1))
    completed = await wait_state(manager, view["session_id"], "completed")
    utterances = [event for event in completed["events"] if event["type"] == "utterance"]
    assert utterances and utterances[0]["actor"] == "doctor"
    assert all(event["source"] == "ai" and event["actor"] != "trainee" for event in utterances)
    report = await manager.report(view["session_id"])
    assert "示范" in report["content"] and "非学员成绩" in report["content"]


async def test_live_creation_waits_before_any_model_call(tmp_path):
    repository = TrainingRepository(tmp_path / "live.sqlite3")
    manager = TrainingManager(
        repository, settings=Settings(model_mode="live", model="unused", api_key="unused")
    )
    await manager.startup()
    try:
        view, created = await manager.create(create_command())
        assert created and view["state"] == "awaiting_input"
        assert view["model_mode"] == "live"
        assert not [event for event in view["events"] if event["type"] == "utterance"]
        assert (await manager.list_sessions())["total"] == 1
    finally:
        await manager.shutdown()
        await repository.dispose()


async def test_report_retry_only_repeats_review_not_the_conversation(manager, monkeypatch):
    original = manager._generate_report

    async def broken(record):
        raise RuntimeError("internal-secret-must-not-escape")

    monkeypatch.setattr(manager, "_generate_report", broken)
    view, _ = await manager.create(create_command(max_rounds=1))
    await manager.submit(view["session_id"], message())
    failed = await wait_state(manager, view["session_id"], "failed")
    assert failed["phase"] == "report" and failed["report_state"] == "failed"
    assert "internal-secret" not in str(failed)
    old_utterances = [event for event in failed["events"] if event["type"] == "utterance"]
    monkeypatch.setattr(manager, "_generate_report", original)
    await manager.retry_report(view["session_id"])
    completed = await wait_state(manager, view["session_id"], "completed")
    assert [event for event in completed["events"] if event["type"] == "utterance"] == old_utterances
    assert completed["completed_rounds"] == 1


@pytest.mark.parametrize("next_action", ["submit", "finish"])
async def test_committed_waiting_state_hands_off_to_next_accepted_operation(
    manager, monkeypatch, next_action
):
    committed = asyncio.Event()
    release = asyncio.Event()
    original = manager.repository.transact

    async def paused_transaction(*args, **kwargs):
        result = await original(*args, **kwargs)
        record = result.record
        if (
            not committed.is_set()
            and record.completed_rounds == 1
            and record.snapshot.state.value == "awaiting_input"
        ):
            committed.set()
            await release.wait()
        return result

    monkeypatch.setattr(manager.repository, "transact", paused_transaction)
    view, _ = await manager.create(create_command(max_rounds=2))
    await manager.submit(view["session_id"], message())
    async with asyncio.timeout(5):
        await committed.wait()
    if next_action == "submit":
        await manager.submit(view["session_id"], message("请妈妈谈谈刚才的想法", turn=2))
    else:
        await manager.finish(view["session_id"])
    release.set()
    completed = await wait_state(manager, view["session_id"], "completed")
    assert completed["completed_rounds"] == (2 if next_action == "submit" else 1)


async def test_cancel_discards_an_operation_waiting_for_previous_worker_handoff(manager, monkeypatch):
    committed = asyncio.Event()
    release = asyncio.Event()
    original = manager.repository.transact
    generated = []
    original_response = manager.family.respond

    async def count_response(*args, **kwargs):
        generated.append(args[3])
        return await original_response(*args, **kwargs)

    async def paused_transaction(*args, **kwargs):
        result = await original(*args, **kwargs)
        if (
            not committed.is_set()
            and result.record.completed_rounds == 1
            and result.record.snapshot.state.value == "awaiting_input"
        ):
            committed.set()
            await release.wait()
        return result

    monkeypatch.setattr(manager.family, "respond", count_response)
    monkeypatch.setattr(manager.repository, "transact", paused_transaction)
    view, _ = await manager.create(create_command(max_rounds=3))
    await manager.submit(view["session_id"], message())
    async with asyncio.timeout(5):
        await committed.wait()
    await manager.submit(view["session_id"], message("请妈妈回答", turn=2))
    cancelled = await manager.cancel(view["session_id"])
    release.set()
    await asyncio.sleep(0.02)
    assert cancelled["state"] == "cancelled"
    assert generated == [1]
    assert await manager.snapshot(view["session_id"]) == cancelled


async def test_shutdown_during_report_marks_report_failed_not_forever_generating(manager, monkeypatch):
    reporting = asyncio.Event()

    async def pause_report(record):
        reporting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_generate_report", pause_report)
    view, _ = await manager.create(create_command(max_rounds=1))
    await manager.submit(view["session_id"], message())
    async with asyncio.timeout(5):
        await reporting.wait()
    await manager.shutdown()
    interrupted = await manager.snapshot(view["session_id"])
    assert interrupted["state"] == "interrupted"
    assert interrupted["report_state"] == "failed"
    assert (await manager.report(view["session_id"]))["status"] == "failed"


async def test_create_replay_uses_saved_resource_before_checking_current_case(manager, monkeypatch):
    from app.resources import CaseResourceError
    from app.services import training_manager

    command = create_command()
    original, _ = await manager.create(command)

    def unavailable_case(case_id):
        raise CaseResourceError("invalid_case_data")

    monkeypatch.setattr(training_manager, "load_validated_case", unavailable_case)
    replay, created = await manager.create(command)
    assert not created
    assert replay == original
    with pytest.raises(ManagerError) as failure:
        await manager.create(create_command())
    assert failure.value.code == "invalid_case_data"
