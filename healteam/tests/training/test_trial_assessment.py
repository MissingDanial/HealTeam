from __future__ import annotations

import json
from dataclasses import replace

import pytest
from app.services.live_training import LiveOutputError, LiveTrainingEngine
from app.services.training_manager import TrainingManager
from app.settings import Settings
from healteam_memory.contracts import CreateSessionCommand, SessionState, transition_snapshot
from healteam_memory.training_store import Mutation, TrainingRepository

from tests.training.test_live_training import ModelFixture, command, message, wait_state


@pytest.fixture
async def trial(tmp_path):
    repository = TrainingRepository(tmp_path / "trial.sqlite3")
    client = ModelFixture()
    manager = TrainingManager(
        repository,
        settings=Settings(
            model_mode="live", provider="openai", model="fixture-model", api_key="fixture-secret"
        ),
        model_client=client,
        response_delay=0.001,
    )
    await manager.startup()
    try:
        yield manager, client
    finally:
        await manager.shutdown()
        await repository.dispose()


async def completed_trial(manager):
    view, _ = await manager.create(command())
    await manager.submit(view["session_id"], message())
    await wait_state(manager, view["session_id"], "completed")
    return await manager.repository.get(view["session_id"])


def model_output(record):
    assessment = record.report["assessment"]
    return {
        **record.report["structured"],
        "assessment": {"dimensions": assessment["dimensions"], "milestones": assessment["milestones"]},
    }


async def test_trial_report_has_frozen_rubric_scope_and_separate_model_assessment(trial):
    manager, client = trial
    record = await completed_trial(manager)
    report = await manager.report(record.snapshot.session_id)
    assert report["rubric"] == record.config_snapshot["evaluation_rubric"]
    assessment = report["assessment"]
    assert assessment["rubric_hash"] == report["rubric"]["sha256"]
    assert assessment["rubric_version"] == report["rubric"]["rubric_version"]
    visible = [event for event in record.events if event.type.value == "utterance"]
    assert assessment["reviewed_event_seq"] == [event.seq for event in visible]
    assert assessment["total_utterances"] == 2
    assert "assessment" not in report["structured"]
    assert "total_score" not in assessment
    started = [event for event in record.events if event.type.value == "model_call_started"]
    assert [event.payload["max_output_tokens"] for event in started] == [1024, 4096]
    assert len(client.calls) == 2


async def test_mock_report_can_expose_trial_rubric_without_fabricating_ai_judgments(trial):
    manager, client = trial
    manager.settings = Settings()
    record = await completed_trial(manager)
    assert record.report["rubric"]["status"] == "trial_unvalidated"
    assert record.report["assessment"] is None
    assert not client.calls


async def test_case_goal_rubric_is_not_exposed_before_the_saved_report(trial):
    manager, _client = trial
    view, _ = await manager.create(command())
    pending = await manager.report(view["session_id"])
    assert pending["rubric"] is None and pending["assessment"] is None
    record = await manager.repository.get(view["session_id"])
    assert record.config_snapshot["evaluation_rubric"]["milestones"]


async def test_saved_s3_session_does_not_acquire_new_rubric_or_output_budget(trial):
    manager, client = trial
    prepared, _ = await manager.create(command())
    original = await manager.repository.get(prepared["session_id"])
    await manager.cancel(prepared["session_id"])
    config = {
        key: value
        for key, value in original.config_snapshot.items()
        if key not in {"evaluation_rubric", "review_max_tokens"}
    }
    legacy = await manager.repository.create(
        command(), config_snapshot=config, case_snapshot=original.case_snapshot
    )
    session_id = legacy.record.snapshot.session_id
    await manager.repository.transact(
        session_id,
        lambda record: Mutation(snapshot=transition_snapshot(record.snapshot, SessionState.AWAITING_INPUT)),
    )
    await manager.submit(session_id, message())
    await wait_state(manager, session_id, "completed")
    record = await manager.repository.get(session_id)
    assert record.report["rubric"] is None and record.report["assessment"] is None
    started = [event for event in record.events if event.type.value == "model_call_started"]
    assert [event.payload["max_output_tokens"] for event in started] == [1024, 1024]
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "defect",
    [
        "bool_rating",
        "high_rating",
        "duplicate_dimension",
        "missing_dimension",
        "unknown_milestone",
        "duplicate_milestone",
        "family_only_rating",
        "unknown_evidence",
        "duplicate_evidence",
        "wrong_milestone_actor",
        "spoofed_scope",
    ],
)
async def test_trial_assessment_rejects_invalid_sets_scope_and_attribution(trial, defect):
    manager, _client = trial
    record = await completed_trial(manager)
    engine = manager._live_engine(record)
    prompt = engine.prepare(record, "review")
    output = model_output(record)
    assessment = output["assessment"]
    family_seq = next(
        event.seq
        for event in record.events
        if event.type.value == "utterance" and event.actor.value == "father"
    )
    if defect == "bool_rating":
        assessment["dimensions"][0]["rating"] = True
    elif defect == "high_rating":
        assessment["dimensions"][0]["rating"] = 3
    elif defect == "duplicate_dimension":
        assessment["dimensions"][1]["dimension_id"] = assessment["dimensions"][0]["dimension_id"]
    elif defect == "missing_dimension":
        assessment["dimensions"].pop()
    elif defect == "unknown_milestone":
        assessment["milestones"][0]["milestone_id"] = "M999"
    elif defect == "duplicate_milestone":
        assessment["milestones"][1]["milestone_id"] = assessment["milestones"][0]["milestone_id"]
    elif defect == "family_only_rating":
        assessment["dimensions"][0]["evidence_seq"] = [family_seq]
    elif defect == "unknown_evidence":
        assessment["dimensions"][0]["evidence_seq"] = [99999]
    elif defect == "duplicate_evidence":
        seq = assessment["dimensions"][0]["evidence_seq"][0]
        assessment["dimensions"][0]["evidence_seq"] = [seq, seq]
    elif defect == "wrong_milestone_actor":
        assessment["milestones"][0].update(
            status="observed", evidence_seq=assessment["dimensions"][0]["evidence_seq"]
        )
    elif defect == "spoofed_scope":
        assessment["reviewed_event_seq"] = [99999]
    with pytest.raises(LiveOutputError):
        engine.parse_review(json.dumps(output), prompt)


async def test_unshown_dimension_remains_unknown_instead_of_zero(trial):
    manager, _client = trial
    record = await completed_trial(manager)
    output = model_output(record)
    for item in output["assessment"]["dimensions"]:
        item.update(rating=None, evidence_seq=[], rationale="当前发言没有充分展示这项行为，无法判断。")
    prompt = manager._live_engine(record).prepare(record, "review")
    parsed = manager._live_engine(record).parse_review(json.dumps(output), prompt)
    assert all(item["rating"] is None for item in parsed["assessment"]["dimensions"])


async def test_observed_joint_milestone_needs_both_parents_actual_evidence(trial):
    manager, _client = trial
    request = CreateSessionCommand.model_validate({**command().model_dump(), "school": "structural"})
    view, _ = await manager.create(request)
    await manager.submit(view["session_id"], message("请爸爸、妈妈分别说说。"))
    await wait_state(manager, view["session_id"], "completed")
    record = await manager.repository.get(view["session_id"])
    engine = manager._live_engine(record)
    prompt = engine.prepare(record, "review")
    output = model_output(record)
    parents = {
        event.actor.value: event.seq
        for event in record.events
        if event.type.value == "utterance" and event.actor.value in {"father", "mother"}
    }
    milestone = next(item for item in output["assessment"]["milestones"] if item["milestone_id"] == "M4")
    milestone.update(status="observed", evidence_seq=[parents["father"]])
    with pytest.raises(LiveOutputError):
        engine.parse_review(json.dumps(output), prompt)
    milestone["evidence_seq"].append(parents["mother"])
    parsed = engine.parse_review(json.dumps(output), prompt)
    assert (
        next(item for item in parsed["assessment"]["milestones"] if item["milestone_id"] == "M4")["status"]
        == "observed"
    )


async def test_report_scope_comes_from_actual_trimmed_window_not_model(trial):
    manager, _client = trial
    record = await completed_trial(manager)
    prompt = manager._live_engine(record).prepare(record, "review")
    data = json.loads(prompt.messages[0]["content"])
    data["events"] = data["events"][-1:]
    limit = len(prompt.system) + len(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    engine = LiveTrainingEngine(max_tokens=1024, review_max_tokens=4096, context_max_chars=limit)
    trimmed = engine.prepare(record, "review")
    sent = json.loads(trimmed.messages[0]["content"])["events"]
    assert len(sent) == 1
    output = model_output(record)
    for point in (*output["observations"], *output["next_steps"]):
        point["evidence_seq"] = [sent[0]["seq"]]
    for item in output["assessment"]["dimensions"]:
        item.update(rating=None, evidence_seq=[])
    parsed = engine.parse_review(json.dumps(output), trimmed)
    assert parsed["assessment"]["reviewed_event_seq"] == [sent[0]["seq"]]
    assert parsed["assessment"]["total_utterances"] == 2


async def test_tampered_frozen_rubric_is_not_replaced_with_current_file(trial):
    manager, _client = trial
    record = await completed_trial(manager)
    changed = json.loads(json.dumps(record.config_snapshot))
    changed["evaluation_rubric"]["dimensions"][0]["label"] = "被修改的量规"
    with pytest.raises(LiveOutputError) as failure:
        manager._live_engine(record).prepare(replace(record, config_snapshot=changed), "review")
    assert failure.value.code == "evaluation_rubric_unavailable"
