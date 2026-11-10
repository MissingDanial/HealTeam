"""Append-only review persistence and non-destructive schema v1 migration."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from hashlib import sha256
from uuid import uuid4

import pytest
import pytest_asyncio
from healteam_memory.contracts import CreateSessionCommand, transition_snapshot
from healteam_memory.evaluation_contracts import ReviewSubmission
from healteam_memory.review_store import (
    InvalidReviewEvidenceError,
    ReviewBindingConflictError,
    ReviewRevisionConflictError,
    ReviewUnavailableError,
    report_fingerprint,
    review_context,
)
from healteam_memory.training_store import (
    ConflictError,
    EventDraft,
    Mutation,
    RuntimeNotOwnedError,
    SchemaVersionError,
    TrainingRepository,
)
from sqlalchemy import event


async def stored_report(repository, *, rubric=None, model_mode="live", mode="training"):
    result = await repository.create(
        CreateSessionCommand(
            client_request_id=uuid4(), case_id="teen-truancy-01", school="satir", max_rounds=1, mode=mode
        ),
        config_snapshot={
            "model_mode": model_mode,
            "provider": "local-test",
            "model": "fixture",
            **({"evaluation_rubric": rubric} if rubric else {}),
        },
        case_snapshot={"case_id": "teen-truancy-01", "version": "1.0", "title": "Preserved case"},
    )
    session_id = result.record.snapshot.session_id

    def submit(record):
        target = transition_snapshot(record.snapshot, "running").model_copy(update={"current_round": 1})
        return Mutation(
            snapshot=target,
            events=[
                EventDraft(
                    round=1,
                    type="utterance",
                    actor="trainee" if mode == "training" else "doctor",
                    source="human" if mode == "training" else "ai",
                    visibility="public",
                    payload={"content": "请说说你的感受。"},
                ),
                EventDraft(
                    round=1,
                    type="utterance",
                    actor="father",
                    source="ai",
                    visibility="public",
                    payload={"content": "我愿意先谈谈自己的担心。"},
                ),
            ],
            receipt={"accepted_turn": 1},
        )

    await repository.transact(
        session_id, submit, receipt_key="learner-1", receipt_payload={"content": "请说说你的感受。"}
    )
    await repository.transact(
        session_id,
        lambda record: Mutation(
            snapshot=transition_snapshot(record.snapshot, "running", phase="supervision"), completed_rounds=1
        ),
    )
    await repository.transact(
        session_id,
        lambda record: Mutation(
            snapshot=transition_snapshot(
                record.snapshot, "running", phase="report", report_state="generating"
            )
        ),
    )
    await repository.transact(
        session_id,
        lambda record: Mutation(
            snapshot=transition_snapshot(record.snapshot, "completed", report_state="ready"),
            report={
                "session_id": str(session_id),
                "kind": "ai_review" if model_mode == "live" else "workflow_check",
                "status": "ready",
                "content": "Preserved report",
                "structured": {"summary": "A saved review", "observations": [], "next_steps": []},
                **({"rubric": rubric} if rubric else {}),
            },
        ),
    )
    return session_id


def trial_rubric():
    rubric = {
        "rubric_id": "trial-observation",
        "rubric_version": "1",
        "status": "trial_unvalidated",
        "school": "satir",
        "dimensions": [
            {"dimension_id": name}
            for name in ("participation", "exploration", "school_consistency", "collaborative_next_step")
        ],
        "milestones": [{"milestone_id": "father_expression", "required_actors": ["father"]}],
    }
    rubric["sha256"] = sha256(
        json.dumps(rubric, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return rubric


async def submission(repository, session_id, *, revision=0, **changes):
    record = await repository.get(session_id)
    return ReviewSubmission.model_validate(
        {
            "client_review_id": str(uuid4()),
            "expected_revision": revision,
            "report_hash": report_fingerprint(record.report),
            "rubric_hash": record.report["rubric"]["sha256"],
            "reviewer_name": "本地复核者",
            "notes": "试行教学复核，不代表资质认证。",
            "dimensions": [
                {
                    "dimension_id": item["dimension_id"],
                    "rating": 1,
                    "rationale": "引用学员的一次明确邀请。",
                    "evidence_seq": [2],
                }
                for item in record.report["rubric"]["dimensions"]
            ],
            "milestones": [
                {
                    "milestone_id": "father_expression",
                    "status": "observed",
                    "rationale": "父亲公开表达了担心。",
                    "evidence_seq": [3],
                }
            ],
            **changes,
        }
    )


@pytest_asyncio.fixture
async def reviewable(tmp_path):
    repository = TrainingRepository(tmp_path / "reviews.sqlite3")
    session_id = await stored_report(repository, rubric=trial_rubric())
    try:
        yield repository, session_id
    finally:
        await repository.dispose()


async def test_review_preserves_original_report_events_and_session_revision(reviewable):
    repository, session_id = reviewable
    before = await repository.get(session_id)
    context = review_context(before)
    assert context == {
        "report_hash": report_fingerprint(before.report),
        "rubric_hash": before.report["rubric"]["sha256"],
    }
    saved = await repository.submit_review(session_id, await submission(repository, session_id))
    assert saved.review["revision"] == 1 and not saved.replayed
    assert saved.review["reviewer_identity_verified"] is False
    assert await repository.get(session_id) == before
    assert await repository.list_reviews(session_id) == [saved.review]
    await repository.dispose()
    reopened = TrainingRepository(repository.path)
    try:
        assert await reopened.list_reviews(session_id) == [saved.review]
        assert await reopened.get(session_id) == before
    finally:
        await reopened.dispose()


async def test_saved_review_can_be_read_from_a_fresh_python_process(reviewable):
    repository, session_id = reviewable
    saved = await repository.submit_review(session_id, await submission(repository, session_id))
    script = """
import asyncio, json, sys
from healteam_memory.training_store import TrainingRepository
async def main():
    repository=TrainingRepository(sys.argv[1])
    try:
        print(json.dumps(await repository.list_reviews(sys.argv[2]),ensure_ascii=False))
    finally:
        await repository.dispose()
asyncio.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, str(repository.path), str(session_id)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
        timeout=15,
        check=True,
    )
    assert json.loads(result.stdout) == [saved.review]


async def test_review_replay_precedes_stale_revision_and_changed_payload_is_conflict(reviewable):
    repository, session_id = reviewable
    command = await submission(repository, session_id)
    first = await repository.submit_review(session_id, command)
    again = await repository.submit_review(session_id, command)
    assert again.replayed and again.review == first.review
    changed = ReviewSubmission.model_validate({**command.model_dump(), "notes": "Different content"})
    with pytest.raises(ConflictError, match="idempotency_conflict"):
        await repository.submit_review(session_id, changed)
    assert len(await repository.list_reviews(session_id)) == 1


async def test_review_revisions_append_history_and_reject_stale_revision(reviewable):
    repository, session_id = reviewable
    first = await repository.submit_review(session_id, await submission(repository, session_id))
    with pytest.raises(ReviewRevisionConflictError) as caught:
        await repository.submit_review(session_id, await submission(repository, session_id))
    assert caught.value.current_revision == 1
    second = await repository.submit_review(
        session_id, await submission(repository, session_id, revision=1, notes="修订后保留第一次记录。")
    )
    assert [item["revision"] for item in await repository.list_reviews(session_id)] == [1, 2]
    assert first.review != second.review
    samples = await repository.list_calibration_samples()
    assert len(samples) == 1 and samples[0]["latest_review"] == second.review
    assert set(samples[0]) == {
        "session_id",
        "school",
        "mode",
        "model_mode",
        "provider",
        "model",
        "report",
        "latest_review",
    }


@pytest.mark.parametrize("binding", ["report_hash", "rubric_hash"])
async def test_changed_review_binding_rolls_back_without_a_revision(reviewable, binding):
    repository, session_id = reviewable
    command = await submission(repository, session_id, **{binding: "0" * 64})
    with pytest.raises(ReviewBindingConflictError):
        await repository.submit_review(session_id, command)
    assert await repository.list_reviews(session_id) == []


@pytest.mark.parametrize(
    "fault",
    ["private_sequence", "family_only_training_rating", "missing_milestone_actor", "wrong_milestone_id"],
)
async def test_invalid_review_evidence_is_rejected_atomically(reviewable, fault):
    repository, session_id = reviewable
    payload = (await submission(repository, session_id)).model_dump(mode="json")
    if fault == "private_sequence":
        payload["dimensions"][0]["evidence_seq"] = [1]
    elif fault == "family_only_training_rating":
        payload["dimensions"][0]["evidence_seq"] = [3]
    elif fault == "missing_milestone_actor":
        payload["milestones"][0]["evidence_seq"] = [2]
    else:
        payload["milestones"][0]["milestone_id"] = "invented_goal"
    with pytest.raises(InvalidReviewEvidenceError):
        await repository.submit_review(session_id, ReviewSubmission.model_validate(payload))
    assert await repository.list_reviews(session_id) == []


async def test_simultaneous_reviews_from_independent_repositories_have_one_winner(reviewable):
    repository, session_id = reviewable
    other = TrainingRepository(repository.path)
    try:
        commands = [await submission(repository, session_id) for _ in range(2)]
        results = await asyncio.gather(
            repository.submit_review(session_id, commands[0]),
            other.submit_review(session_id, commands[1]),
            return_exceptions=True,
        )
        assert sum(isinstance(item, ReviewRevisionConflictError) for item in results) == 1
        assert len(await repository.list_reviews(session_id)) == 1
    finally:
        await other.dispose()


async def test_a_new_active_training_does_not_block_saved_report_review(reviewable):
    repository, session_id = reviewable
    active = (
        await repository.create(
            CreateSessionCommand(client_request_id=uuid4(), case_id="teen-truancy-01", school="satir"),
            config_snapshot={"model_mode": "mock"},
            case_snapshot={"case_id": "teen-truancy-01", "version": "1"},
        )
    ).record
    await repository.submit_review(session_id, await submission(repository, session_id))
    assert await repository.get(active.snapshot.session_id) == active


@pytest.mark.parametrize("model_mode,mode", [("mock", "training"), ("live", "demo")])
async def test_mock_manual_and_demo_observation_review_are_supported(tmp_path, model_mode, mode):
    repository = TrainingRepository(tmp_path / "observations.sqlite3")
    try:
        session_id = await stored_report(repository, rubric=trial_rubric(), model_mode=model_mode, mode=mode)
        saved = await repository.submit_review(session_id, await submission(repository, session_id))
        sample = (await repository.list_calibration_samples())[0]
        assert saved.review["revision"] == 1
        assert sample["mode"] == mode and sample["model_mode"] == model_mode
    finally:
        await repository.dispose()


async def test_legacy_report_without_rubric_is_readable_but_not_reviewable(tmp_path):
    repository = TrainingRepository(tmp_path / "legacy-report.sqlite3")
    try:
        session_id = await stored_report(repository)
        record = await repository.get(session_id)
        assert record.report["content"] == "Preserved report"
        with pytest.raises(ReviewUnavailableError):
            review_context(record)
        assert await repository.list_reviews(session_id) == []
        assert (await repository.list_calibration_samples())[0]["latest_review"] is None
    finally:
        await repository.dispose()


async def test_review_ownership_loss_after_insert_rolls_back_revision(reviewable):
    repository, session_id = reviewable
    owned = True
    repository._write_guard = lambda: owned

    def lose_ownership(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal owned
        if statement.startswith("INSERT INTO training_expert_reviews"):
            owned = False

    event.listen(repository.engine.sync_engine, "after_cursor_execute", lose_ownership)
    try:
        with pytest.raises(RuntimeNotOwnedError):
            await repository.submit_review(session_id, await submission(repository, session_id))
        assert await repository.list_reviews(session_id) == []
    finally:
        owned = True
        event.remove(repository.engine.sync_engine, "after_cursor_execute", lose_ownership)


@pytest.mark.parametrize("fault", ["rubric_hash", "wrong_school", "assessment_binding"])
async def test_corrupted_report_binding_is_never_reviewable(reviewable, fault):
    repository, session_id = reviewable
    record = await repository.get(session_id)
    if fault == "rubric_hash":
        record.report["rubric"]["sha256"] = "0" * 64
    elif fault == "wrong_school":
        rubric = record.report["rubric"]
        rubric["school"] = "bowen"
        rubric["sha256"] = sha256(
            json.dumps(
                {key: value for key, value in rubric.items() if key != "sha256"},
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        record.config_snapshot["evaluation_rubric"] = rubric
    else:
        record.report["assessment"] = {"rubric_version": "1", "rubric_hash": "0" * 64}
    with pytest.raises(ReviewUnavailableError):
        review_context(record)


def downgrade_fixture_to_v1(path):
    # This removes only the empty, newly introduced table. The remaining
    # schema/data are the actual S1-S3 tables, not invented migration stubs.
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE training_expert_reviews")
        connection.execute("PRAGMA user_version = 1")


def base_rows(path):
    with sqlite3.connect(path) as connection:
        return {
            name: connection.execute(f'SELECT * FROM "{name}" ORDER BY 1, 2').fetchall()
            for name in ("training_sessions", "training_events", "training_receipts")
        }


def test_v1_migration_preserves_sessions_reports_receipts_and_every_event(tmp_path):
    async def scenario():
        path = tmp_path / "legacy.sqlite3"
        repository = TrainingRepository(path)
        session_id = await stored_report(repository)
        before = await repository.get(session_id)
        await repository.dispose()
        downgrade_fixture_to_v1(path)
        rows_before = base_rows(path)
        reopened = TrainingRepository(path)
        try:
            await reopened.initialize()
            assert await reopened.get(session_id) == before
            assert base_rows(path) == rows_before
            with sqlite3.connect(path) as connection:
                assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
                assert connection.execute("SELECT count(*) FROM training_expert_reviews").fetchone()[0] == 0
        finally:
            await reopened.dispose()

    asyncio.run(scenario())


def test_migration_ownership_loss_rolls_back_new_table_and_version(tmp_path):
    async def scenario():
        path = tmp_path / "legacy.sqlite3"
        initial = TrainingRepository(path)
        await stored_report(initial)
        await initial.dispose()
        downgrade_fixture_to_v1(path)
        before = base_rows(path)
        checks = 0

        def owner():
            nonlocal checks
            checks += 1
            return checks == 1

        repository = TrainingRepository(path, write_guard=owner)
        try:
            with pytest.raises(RuntimeNotOwnedError):
                await repository.initialize()
            assert base_rows(path) == before
            with sqlite3.connect(path) as connection:
                assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
                assert (
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE name='training_expert_reviews'"
                    ).fetchone()
                    is None
                )
        finally:
            await repository.dispose()

    asyncio.run(scenario())


def test_broken_v1_columns_are_rejected_without_migration(tmp_path):
    path = tmp_path / "broken.sqlite3"
    with sqlite3.connect(path) as connection:
        for name in ("training_sessions", "training_events", "training_receipts"):
            connection.execute(f'CREATE TABLE "{name}" (important TEXT)')
        connection.execute("INSERT INTO training_sessions VALUES ('keep this')")
        connection.execute("PRAGMA user_version = 1")

    async def scenario():
        repository = TrainingRepository(path)
        try:
            with pytest.raises(SchemaVersionError):
                await repository.initialize()
        finally:
            await repository.dispose()

    asyncio.run(scenario())
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT important FROM training_sessions").fetchone()[0] == "keep this"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_two_repository_instances_can_race_to_migrate_only_once(tmp_path):
    async def scenario():
        path = tmp_path / "legacy.sqlite3"
        initial = TrainingRepository(path)
        await stored_report(initial)
        await initial.dispose()
        downgrade_fixture_to_v1(path)
        before = base_rows(path)
        first, second = TrainingRepository(path), TrainingRepository(path)
        try:
            await asyncio.gather(first.initialize(), second.initialize())
            assert base_rows(path) == before
            with sqlite3.connect(path) as connection:
                assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        finally:
            await first.dispose()
            await second.dispose()

    asyncio.run(scenario())
