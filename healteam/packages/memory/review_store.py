"""Append-only human reviews bound to saved AI reports and frozen rubrics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from .contracts import EventVisibility, SessionMode, SessionSnapshot, SessionState, StreamEventType
from .evaluation_contracts import DimensionId
from .training_models import ExpertReviewRow, TrainingSessionRow
from .training_store import ConflictError, StoreError, TrainingRecord, _fingerprint, _json, _now


class ReviewUnavailableError(StoreError):
    code = "review_unavailable"
    status_code = 409


class ReviewRevisionConflictError(StoreError):
    code = "review_revision_conflict"
    status_code = 409

    def __init__(self, *, current_revision: int, **kwargs: Any):
        self.current_revision = current_revision
        super().__init__(**kwargs)


class ReviewBindingConflictError(StoreError):
    code = "review_binding_conflict"
    status_code = 409


class InvalidReviewEvidenceError(StoreError):
    code = "review_evidence_invalid"
    status_code = 422


@dataclass(frozen=True)
class ReviewMutationResult:
    review: dict[str, Any]
    replayed: bool = False


def report_fingerprint(report: dict[str, Any]) -> str:
    """Hash the exact persisted report, not a reserialized public projection."""
    return sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _reviewable(record: TrainingRecord) -> dict[str, Any]:
    report = record.report
    if (
        record.snapshot.state != SessionState.COMPLETED
        or not isinstance(report, dict)
        or report.get("kind") not in {"workflow_check", "ai_review"}
        or not isinstance(report.get("rubric"), dict)
    ):
        raise ReviewUnavailableError(session_id=record.snapshot.session_id)
    rubric = report["rubric"]
    try:
        dimension_ids = [item["dimension_id"] for item in rubric["dimensions"]]
        milestone_ids = [item["milestone_id"] for item in rubric["milestones"]]
        valid_ids = (
            len(dimension_ids) == 4
            and set(dimension_ids) == set(DimensionId)
            and len(milestone_ids) == len(set(milestone_ids))
        )
    except (KeyError, TypeError):
        valid_ids = False
    if not valid_ids:
        raise ReviewUnavailableError(session_id=record.snapshot.session_id)
    digest = sha256(
        json.dumps(
            {key: value for key, value in rubric.items() if key != "sha256"},
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if (
        not isinstance(rubric.get("sha256"), str)
        or rubric["sha256"] != digest
        or rubric.get("school") != record.snapshot.school.value
        or rubric.get("status") != "trial_unvalidated"
        or not isinstance(rubric.get("dimensions"), list)
        or not isinstance(rubric.get("milestones"), list)
        or rubric != record.config_snapshot.get("evaluation_rubric")
    ):
        raise ReviewUnavailableError(session_id=record.snapshot.session_id)
    assessment = report.get("assessment")
    if assessment is not None and (
        not isinstance(assessment, dict)
        or assessment.get("rubric_hash") != rubric["sha256"]
        or assessment.get("rubric_version") != rubric.get("rubric_version")
    ):
        raise ReviewUnavailableError(session_id=record.snapshot.session_id)
    return rubric


def review_context(record: TrainingRecord) -> dict[str, str]:
    rubric = _reviewable(record)
    return {"report_hash": report_fingerprint(record.report), "rubric_hash": rubric["sha256"]}


def _validate_evidence(record: TrainingRecord, submission: Any, rubric: dict[str, Any]) -> None:
    dimensions = {item["dimension_id"] for item in rubric["dimensions"]}
    milestones = {item["milestone_id"]: item for item in rubric["milestones"]}
    if (
        len(submission.dimensions) != len(dimensions)
        or {item.dimension_id for item in submission.dimensions} != dimensions
        or len(submission.milestones) != len(milestones)
        or {item.milestone_id for item in submission.milestones} != set(milestones)
    ):
        raise InvalidReviewEvidenceError(session_id=record.snapshot.session_id)
    visible = {
        event.seq: event.actor.value
        for event in record.events
        if event.type == StreamEventType.UTTERANCE and event.visibility == EventVisibility.PUBLIC
    }
    for item in (*submission.dimensions, *submission.milestones):
        if len(item.evidence_seq) != len(set(item.evidence_seq)) or any(
            seq not in visible for seq in item.evidence_seq
        ):
            raise InvalidReviewEvidenceError(session_id=record.snapshot.session_id)
    for item in submission.dimensions:
        if item.rating is not None and (
            not item.evidence_seq
            or (
                record.snapshot.mode == SessionMode.TRAINING
                and "trainee" not in {visible[seq] for seq in item.evidence_seq}
            )
        ):
            raise InvalidReviewEvidenceError(session_id=record.snapshot.session_id)
    for item in submission.milestones:
        if item.status == "observed":
            required = set(milestones[item.milestone_id].get("required_actors", []))
            if not item.evidence_seq or not required.issubset({visible[seq] for seq in item.evidence_seq}):
                raise InvalidReviewEvidenceError(session_id=record.snapshot.session_id)


async def submit_review(repository: Any, session_id: str | UUID, submission: Any) -> ReviewMutationResult:
    from .evaluation_contracts import ReviewRecord, ReviewSubmission

    command = ReviewSubmission.model_validate(submission)
    payload = command.model_dump(mode="json")
    fingerprint = _fingerprint(payload)
    await repository.initialize()
    async with repository._lock(), repository._sessions() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        try:
            repository._check_write_ownership()
            row = await repository._require(session, session_id)
            duplicate = await session.scalar(
                select(ExpertReviewRow).where(
                    ExpertReviewRow.session_id == row.session_id,
                    ExpertReviewRow.client_review_id == str(command.client_review_id),
                )
            )
            if duplicate is not None:
                if duplicate.request_fingerprint != fingerprint:
                    raise ConflictError(session_id=row.session_id)
                repository._check_write_ownership()
                await session.commit()
                return ReviewMutationResult(json.loads(duplicate.review_json), replayed=True)
            record = await repository._record(session, row)
            rubric = _reviewable(record)
            if (
                command.report_hash != report_fingerprint(record.report)
                or command.rubric_hash != rubric["sha256"]
            ):
                raise ReviewBindingConflictError(session_id=row.session_id)
            latest = await session.scalar(
                select(ExpertReviewRow)
                .where(ExpertReviewRow.session_id == row.session_id)
                .order_by(ExpertReviewRow.revision.desc())
                .limit(1)
            )
            revision = latest.revision if latest is not None else 0
            if command.expected_revision != revision:
                raise ReviewRevisionConflictError(current_revision=revision, session_id=row.session_id)
            _validate_evidence(record, command, rubric)
            repository._check_write_ownership()
            created_at = _now()
            view = ReviewRecord.model_validate(
                {
                    **{key: value for key, value in payload.items() if key != "expected_revision"},
                    "review_id": str(uuid4()),
                    "session_id": row.session_id,
                    "revision": revision + 1,
                    "created_at": created_at,
                }
            ).model_dump(mode="json")
            session.add(
                ExpertReviewRow(
                    review_id=view["review_id"],
                    session_id=row.session_id,
                    revision=revision + 1,
                    client_review_id=str(command.client_review_id),
                    request_fingerprint=fingerprint,
                    report_hash=command.report_hash,
                    rubric_hash=command.rubric_hash,
                    submission_json=_json(payload),
                    review_json=_json(view),
                    created_at=created_at,
                )
            )
            await session.flush()
            repository._check_write_ownership()
            await session.commit()
            return ReviewMutationResult(view)
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictError("review violates a revision constraint", session_id=session_id) from exc
        except BaseException:
            await session.rollback()
            raise


async def list_reviews(repository: Any, session_id: str | UUID) -> list[dict[str, Any]]:
    await repository.initialize()
    async with repository._sessions() as session:
        await session.execute(text("BEGIN"))
        row = await repository._require(session, session_id)
        rows = (
            await session.scalars(
                select(ExpertReviewRow)
                .where(ExpertReviewRow.session_id == row.session_id)
                .order_by(ExpertReviewRow.revision)
            )
        ).all()
        return [json.loads(item.review_json) for item in rows]


async def list_calibration_samples(repository: Any) -> list[dict[str, Any]]:
    """One read view, latest revision only; no case/config/event disclosure."""
    await repository.initialize()
    async with repository._sessions() as session:
        await session.execute(text("BEGIN"))
        rows = (
            await session.scalars(
                select(TrainingSessionRow)
                .where(TrainingSessionRow.report_json.is_not(None))
                .order_by(TrainingSessionRow.created_at, TrainingSessionRow.session_id)
            )
        ).all()
        reviews = (
            await session.scalars(
                select(ExpertReviewRow).order_by(ExpertReviewRow.session_id, ExpertReviewRow.revision.desc())
            )
        ).all()
        latest: dict[str, dict[str, Any]] = {}
        for review in reviews:
            latest.setdefault(review.session_id, json.loads(review.review_json))
        result = []
        for row in rows:
            snapshot = SessionSnapshot.model_validate_json(row.snapshot_json)
            config = json.loads(row.config_json)
            result.append(
                {
                    "session_id": row.session_id,
                    "school": snapshot.school.value,
                    "mode": snapshot.mode.value,
                    "model_mode": snapshot.model_mode.value,
                    "provider": config.get("provider") if snapshot.model_mode.value == "live" else None,
                    "model": config.get("model") if snapshot.model_mode.value == "live" else None,
                    "report": json.loads(row.report_json),
                    "latest_review": latest.get(row.session_id),
                }
            )
        return result


__all__ = [
    "ReviewUnavailableError",
    "ReviewRevisionConflictError",
    "ReviewBindingConflictError",
    "InvalidReviewEvidenceError",
    "ReviewMutationResult",
    "report_fingerprint",
    "review_context",
    "submit_review",
    "list_reviews",
    "list_calibration_samples",
]
