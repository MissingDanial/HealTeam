"""Transactional, file-backed repository for one local learner's training.

Every operation uses its own AsyncSession. SQLite BEGIN IMMEDIATE serializes
writers across repository instances/processes; a per-event-loop lock avoids
needless contention within an instance. The active-owner unique index and
revision compare-and-swap are additional guards, not substitutes for a commit.
No model calls or other awaits belong inside a mutation callback.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from weakref import WeakKeyDictionary

from sqlalchemy import event, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .contracts import (
    CreateSessionCommand,
    EventEnvelope,
    EventSource,
    EventVisibility,
    ReportState,
    SessionPhase,
    SessionSnapshot,
    SessionState,
    StreamEventType,
    transition_snapshot,
)
from .schema import Actor
from .training_models import (
    ExpertReviewRow,
    TrainingBase,
    TrainingEventRow,
    TrainingReceiptRow,
    TrainingSessionRow,
)

SCHEMA_VERSION = 2
ACTIVE_STATES = {
    SessionState.CREATED,
    SessionState.AWAITING_INPUT,
    SessionState.RUNNING,
    SessionState.CANCELLING,
}


class StoreError(RuntimeError):
    code = "training_store_error"
    status_code = 500

    def __init__(self, message: str | None = None, *, session_id: UUID | str | None = None):
        self.session_id = UUID(str(session_id)) if session_id is not None else None
        super().__init__(message or self.code)


class NotFoundError(StoreError):
    code = "session_not_found"
    status_code = 404


class ConflictError(StoreError):
    code = "idempotency_conflict"
    status_code = 409


class BusyError(ConflictError):
    code = "active_session_exists"


class SchemaVersionError(StoreError):
    code = "unsupported_database_version"


class RuntimeNotOwnedError(StoreError):
    code = "runtime_not_owned"
    status_code = 503


@dataclass(frozen=True, kw_only=True)
class EventDraft:
    round: int
    type: StreamEventType | str
    actor: Actor | str
    source: EventSource | str
    visibility: EventVisibility | str
    payload: dict[str, Any]
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class TrainingRecord:
    snapshot: SessionSnapshot
    config_snapshot: dict[str, Any]
    case_snapshot: dict[str, Any]
    events: tuple[EventEnvelope, ...]
    report: dict[str, Any] | None
    completed_rounds: int
    created_at: str
    updated_at: str
    error_code: str | None
    revision: int


@dataclass(frozen=True)
class Mutation:
    snapshot: SessionSnapshot
    events: Sequence[EventDraft] = field(default_factory=tuple)
    report: dict[str, Any] | None = None
    completed_rounds: int | None = None
    error_code: str | None = None
    receipt: dict[str, Any] | None = None


@dataclass(frozen=True)
class MutationResult:
    record: TrainingRecord
    replayed: bool = False
    receipt: dict[str, Any] | None = None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: Any) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TrainingRepository:
    def __init__(
        self,
        db_path_or_url: str | Path,
        *,
        busy_timeout_seconds: float = 2.0,
        write_guard: Callable[[], bool] | None = None,
    ):
        if write_guard is not None and not callable(write_guard):
            raise TypeError("write_guard must be callable")
        self._write_guard = write_guard
        if (
            isinstance(busy_timeout_seconds, bool)
            or not isinstance(busy_timeout_seconds, (int, float))
            or not 0 < busy_timeout_seconds <= 30
            or not math.isfinite(busy_timeout_seconds)
        ):
            raise ValueError("busy_timeout_seconds must be finite, positive and at most 30")
        busy_timeout_ms = max(1, round(busy_timeout_seconds * 1000))
        self.busy_timeout_seconds = busy_timeout_ms / 1000
        raw = str(db_path_or_url)
        if "://" in raw:
            url = make_url(raw)
            if url.get_backend_name() != "sqlite":
                raise ValueError("S1 requires a file SQLite database")
            if not url.database or url.database == ":memory:" or url.query.get("mode") == "memory":
                raise ValueError("S1 requires a persistent file SQLite database")
            path = Path(url.database).expanduser().resolve()
        else:
            if raw == ":memory:" or not raw.strip():
                raise ValueError("S1 requires a persistent file SQLite database")
            path = Path(raw).expanduser().resolve()
        self.path = path
        # Parent directories are created only by initialize, not by importing.
        url = make_url("sqlite+aiosqlite:///").set(database=str(path))
        self.engine = create_async_engine(
            url, poolclass=NullPool, connect_args={"timeout": self.busy_timeout_seconds}
        )
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self._locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()
        self._initialized = False

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure_connection(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            cursor.close()

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        return lock

    def _check_write_ownership(self) -> None:
        if self._write_guard is not None and not self._write_guard():
            raise RuntimeNotOwnedError()

    async def initialize(self) -> None:
        """Atomically initialize v2 or append reviews to v1; preserve all data."""
        async with self._lock():
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            async with self.engine.connect() as connection:
                await connection.execute(text("BEGIN IMMEDIATE"))
                try:
                    self._check_write_ownership()
                    version = int((await connection.execute(text("PRAGMA user_version"))).scalar_one())
                    if version not in {0, 1, SCHEMA_VERSION}:
                        raise SchemaVersionError(f"Unsupported database schema version {version}")
                    if version == 0:
                        existing = (
                            (
                                await connection.execute(
                                    text(
                                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        if existing:
                            raise SchemaVersionError(
                                "Unversioned non-empty databases cannot be migrated automatically"
                            )
                        await connection.run_sync(TrainingBase.metadata.create_all)
                        await connection.execute(text("PRAGMA user_version = 2"))
                    else:
                        tables = set(
                            (
                                await connection.execute(
                                    text("SELECT name FROM sqlite_master WHERE type='table'")
                                )
                            )
                            .scalars()
                            .all()
                        )
                        required = set(TrainingBase.metadata.tables)
                        if version == 1:
                            required.remove(ExpertReviewRow.__tablename__)
                        if not required.issubset(tables):
                            raise SchemaVersionError("Database is missing required training tables")
                        for table_name in required:
                            columns = {
                                item[1]
                                for item in (
                                    await connection.execute(text(f'PRAGMA table_info("{table_name}")'))
                                ).all()
                            }
                            if not set(TrainingBase.metadata.tables[table_name].columns.keys()).issubset(
                                columns
                            ):
                                raise SchemaVersionError("Database is missing required training columns")
                        if version == 1:
                            if ExpertReviewRow.__tablename__ in tables:
                                raise SchemaVersionError("Version 1 contains an unexpected review table")
                            await connection.run_sync(ExpertReviewRow.__table__.create)
                            await connection.execute(text("PRAGMA user_version = 2"))
                    self._check_write_ownership()
                    await connection.commit()
                except BaseException:
                    await connection.rollback()
                    raise
            self._initialized = True

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def _record(self, session: AsyncSession, row: TrainingSessionRow) -> TrainingRecord:
        events = (
            await session.scalars(
                select(TrainingEventRow)
                .where(TrainingEventRow.session_id == row.session_id)
                .order_by(TrainingEventRow.seq)
            )
        ).all()
        return TrainingRecord(
            snapshot=SessionSnapshot.model_validate_json(row.snapshot_json),
            config_snapshot=json.loads(row.config_json),
            case_snapshot=json.loads(row.case_json),
            events=tuple(EventEnvelope.model_validate_json(item.envelope_json) for item in events),
            report=json.loads(row.report_json) if row.report_json is not None else None,
            completed_rounds=row.completed_rounds,
            created_at=row.created_at,
            updated_at=row.updated_at,
            error_code=row.error_code,
            revision=row.revision,
        )

    async def _require(self, session: AsyncSession, session_id: str | UUID) -> TrainingSessionRow:
        identifier = str(UUID(str(session_id)))
        row = await session.get(TrainingSessionRow, identifier)
        if row is None:
            raise NotFoundError(session_id=identifier)
        return row

    async def lookup_create(self, command: CreateSessionCommand) -> TrainingRecord | None:
        """Recognize accepted requests before rechecking mutable external resources.

        This does not reserve the active slot or replace create's transactional
        duplicate check: two first attempts can both miss this read safely.
        """
        await self.initialize()
        fingerprint = _fingerprint(command.model_dump(mode="json"))
        async with self._sessions() as session:
            await session.execute(text("BEGIN"))
            row = await session.scalar(
                select(TrainingSessionRow).where(
                    TrainingSessionRow.client_request_id == str(command.client_request_id)
                )
            )
            if row is None:
                return None
            if row.request_fingerprint != fingerprint:
                raise ConflictError(session_id=row.session_id)
            return await self._record(session, row)

    async def create(
        self, command: CreateSessionCommand, *, config_snapshot: dict[str, Any], case_snapshot: dict[str, Any]
    ) -> MutationResult:
        await self.initialize()
        request = command.model_dump(mode="json")
        fingerprint = _fingerprint(request)
        async with self._lock(), self._sessions() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            try:
                self._check_write_ownership()
                existing = await session.scalar(
                    select(TrainingSessionRow).where(
                        TrainingSessionRow.client_request_id == str(command.client_request_id)
                    )
                )
                if existing is not None:
                    if existing.request_fingerprint != fingerprint:
                        raise ConflictError(session_id=existing.session_id)
                    record = await self._record(session, existing)
                    self._check_write_ownership()
                    await session.commit()
                    return MutationResult(record=record, replayed=True)
                active = await session.scalar(
                    select(TrainingSessionRow).where(
                        TrainingSessionRow.owner == "local", TrainingSessionRow.active.is_(True)
                    )
                )
                if active is not None:
                    raise BusyError(session_id=active.session_id)
                if case_snapshot.get("case_id") != command.case_id or not case_snapshot.get("version"):
                    raise ValueError("a matching versioned case snapshot is required")
                snapshot = SessionSnapshot(
                    session_id=uuid4(),
                    case_id=command.case_id,
                    case_version=case_snapshot["version"],
                    school=command.school,
                    mode=command.mode,
                    model_mode=config_snapshot.get("model_mode", "mock"),
                    max_rounds=command.max_rounds,
                    last_event_seq=1,
                )
                now = _now()
                row = TrainingSessionRow(
                    session_id=str(snapshot.session_id),
                    owner="local",
                    client_request_id=str(command.client_request_id),
                    request_fingerprint=fingerprint,
                    request_json=_json(request),
                    snapshot_json=snapshot.model_dump_json(),
                    config_json=_json(config_snapshot),
                    case_json=_json(case_snapshot),
                    report_json=None,
                    active=True,
                    completed_rounds=0,
                    created_at=now,
                    updated_at=now,
                    error_code=None,
                    revision=0,
                )
                session.add(row)
                await session.flush()
                envelope = EventEnvelope(
                    session_id=snapshot.session_id,
                    seq=1,
                    round=0,
                    type=StreamEventType.STATUS_CHANGED,
                    actor=Actor.SYSTEM,
                    source=EventSource.SYSTEM,
                    visibility=EventVisibility.PUBLIC,
                    payload={"snapshot": self._view(snapshot, 0, now, now, None)},
                )
                session.add(
                    TrainingEventRow(
                        session_id=row.session_id,
                        seq=1,
                        event_id=str(envelope.event_id),
                        envelope_json=envelope.model_dump_json(),
                    )
                )
                await session.flush()
                record = await self._record(session, row)
                self._check_write_ownership()
                await session.commit()
                return MutationResult(record=record)
            except BaseException:
                await session.rollback()
                raise

    @staticmethod
    def _view(
        snapshot: SessionSnapshot,
        completed_rounds: int,
        created_at: str,
        updated_at: str,
        error_code: str | None,
    ) -> dict[str, Any]:
        return {
            **snapshot.model_dump(mode="json"),
            "completed_rounds": completed_rounds,
            "created_at": created_at,
            "updated_at": updated_at,
            "error_code": error_code,
        }

    async def get(self, session_id: str | UUID) -> TrainingRecord:
        await self.initialize()
        async with self._sessions() as session:
            await session.execute(text("BEGIN"))
            return await self._record(session, await self._require(session, session_id))

    async def list_sessions(self, *, limit: int = 50, offset: int = 0) -> tuple[list[TrainingRecord], int]:
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("invalid session pagination")
        await self.initialize()
        async with self._sessions() as session:
            await session.execute(text("BEGIN"))
            total = int((await session.scalar(select(func.count()).select_from(TrainingSessionRow))) or 0)
            rows = (
                await session.scalars(
                    select(TrainingSessionRow)
                    .order_by(TrainingSessionRow.created_at.desc(), TrainingSessionRow.session_id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return [await self._record(session, row) for row in rows], total

    async def events(
        self, session_id: str | UUID, after_seq: int = 0
    ) -> tuple[SessionSnapshot, list[EventEnvelope]]:
        if type(after_seq) is not int or after_seq < 0:
            raise ValueError("after_seq must be a non-negative integer")
        record = await self.get(session_id)
        return record.snapshot, [event for event in record.events if event.seq > after_seq]

    async def _apply(
        self, session: AsyncSession, row: TrainingSessionRow, record: TrainingRecord, mutation: Mutation
    ) -> TrainingRecord:
        if not isinstance(mutation, Mutation):
            if inspect.iscoroutine(mutation):
                mutation.close()
            raise TypeError("a transaction callback must synchronously return Mutation")
        old, proposed = record.snapshot, mutation.snapshot
        immutable = ("session_id", "case_id", "case_version", "school", "mode", "model_mode", "max_rounds")
        if any(getattr(old, name) != getattr(proposed, name) for name in immutable):
            raise ValueError("session identity and configuration cannot change")
        if proposed.last_event_seq != old.last_event_seq:
            raise ValueError("event sequences are assigned by the repository")
        if proposed.current_round < old.current_round:
            raise ValueError("session rounds cannot move backwards")
        transition_snapshot(old, proposed.state, phase=proposed.phase, report_state=proposed.report_state)
        completed = (
            record.completed_rounds if mutation.completed_rounds is None else mutation.completed_rounds
        )
        if type(completed) is not int or not record.completed_rounds <= completed <= proposed.current_round:
            raise ValueError("completed_rounds must be monotonic and cannot exceed the accepted round")
        retrying_review = (
            old.state in {SessionState.FAILED, SessionState.INTERRUPTED}
            and proposed.state == SessionState.RUNNING
        )
        if retrying_review and (
            proposed.current_round != old.current_round
            or completed != record.completed_rounds
            or any(StreamEventType(draft.type) == StreamEventType.UTTERANCE for draft in mutation.events)
        ):
            raise ValueError("review retry cannot change completed dialogue or its round counters")
        report = record.report if mutation.report is None else mutation.report
        if proposed.state == SessionState.COMPLETED and report is None:
            raise ValueError("completion and its saved report must be committed together")
        if mutation.report is not None and proposed.state != SessionState.COMPLETED:
            raise ValueError("a ready report must be saved with session completion")
        if report is not None and report.get("session_id", str(old.session_id)) != str(old.session_id):
            raise ValueError("report belongs to another session")
        unchanged = (
            proposed == old
            and not mutation.events
            and report == record.report
            and completed == record.completed_rounds
            and mutation.error_code == record.error_code
        )
        if unchanged:
            return record
        retrying_interrupted_review = (
            old.state == SessionState.INTERRUPTED and proposed.state == SessionState.RUNNING
        )
        if old.state in {SessionState.COMPLETED, SessionState.CANCELLED} or (
            old.state == SessionState.INTERRUPTED and not retrying_interrupted_review
        ):
            raise ValueError("terminal session records cannot change")
        if proposed.state in ACTIVE_STATES and not row.active:
            active = await session.scalar(
                select(TrainingSessionRow).where(
                    TrainingSessionRow.owner == "local",
                    TrainingSessionRow.active.is_(True),
                    TrainingSessionRow.session_id != row.session_id,
                )
            )
            if active is not None:
                raise BusyError(session_id=active.session_id)
        now = _now()
        snapshot = SessionSnapshot.model_validate(
            {**proposed.model_dump(), "last_event_seq": old.last_event_seq + len(mutation.events)}
        )
        view = self._view(snapshot, completed, record.created_at, now, mutation.error_code)
        envelopes: list[EventEnvelope] = []
        for index, draft in enumerate(mutation.events, start=old.last_event_seq + 1):
            payload = dict(draft.payload)
            if StreamEventType(draft.type) in {StreamEventType.STATUS_CHANGED, StreamEventType.REPORT_READY}:
                payload["snapshot"] = view
            envelope = EventEnvelope(
                event_id=draft.event_id,
                session_id=snapshot.session_id,
                seq=index,
                round=draft.round,
                type=draft.type,
                actor=draft.actor,
                source=draft.source,
                visibility=draft.visibility,
                timestamp=draft.timestamp,
                payload=payload,
            )
            if envelope.round > snapshot.current_round:
                raise ValueError("event cannot refer to a future round")
            envelopes.append(envelope)
        result = await session.execute(
            update(TrainingSessionRow)
            .where(
                TrainingSessionRow.session_id == row.session_id,
                TrainingSessionRow.revision == record.revision,
            )
            .values(
                snapshot_json=snapshot.model_dump_json(),
                active=snapshot.state in ACTIVE_STATES,
                completed_rounds=completed,
                report_json=_json(report) if report is not None else None,
                updated_at=now,
                error_code=mutation.error_code,
                revision=record.revision + 1,
            )
        )
        if result.rowcount != 1:
            raise ConflictError("session revision changed", session_id=row.session_id)
        for envelope in envelopes:
            session.add(
                TrainingEventRow(
                    session_id=row.session_id,
                    seq=envelope.seq,
                    event_id=str(envelope.event_id),
                    envelope_json=envelope.model_dump_json(),
                )
            )
        await session.flush()
        # Populate a fresh immutable snapshot rather than reusing mutable ORM state.
        return TrainingRecord(
            snapshot,
            json.loads(_json(record.config_snapshot)),
            json.loads(_json(record.case_snapshot)),
            (*record.events, *envelopes),
            report,
            completed,
            record.created_at,
            now,
            mutation.error_code,
            record.revision + 1,
        )

    async def transact(
        self,
        session_id: str | UUID,
        callback: Callable[[TrainingRecord], Mutation],
        *,
        receipt_key: str | None = None,
        receipt_payload: dict[str, Any] | None = None,
    ) -> MutationResult:
        if receipt_key is not None and (not receipt_key or len(receipt_key) > 128 or receipt_payload is None):
            raise ValueError("receipt_key requires a payload and at most 128 characters")
        await self.initialize()
        async with self._lock(), self._sessions() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            try:
                self._check_write_ownership()
                row = await self._require(session, session_id)
                record = await self._record(session, row)
                if receipt_key is not None:
                    receipt_row = await session.get(TrainingReceiptRow, (row.session_id, receipt_key))
                    if receipt_row is not None:
                        if receipt_row.fingerprint != _fingerprint(receipt_payload):
                            raise ConflictError(session_id=row.session_id)
                        self._check_write_ownership()
                        await session.commit()
                        return MutationResult(
                            record=record, replayed=True, receipt=json.loads(receipt_row.response_json)
                        )
                self._check_write_ownership()
                mutation = callback(record)
                updated = await self._apply(session, row, record, mutation)
                receipt = mutation.receipt
                if receipt_key is not None:
                    receipt = (
                        receipt
                        if receipt is not None
                        else {
                            "session_id": str(updated.snapshot.session_id),
                            "snapshot": updated.snapshot.model_dump(mode="json"),
                        }
                    )
                    session.add(
                        TrainingReceiptRow(
                            session_id=row.session_id,
                            receipt_key=receipt_key,
                            fingerprint=_fingerprint(receipt_payload),
                            response_json=_json(receipt),
                        )
                    )
                    await session.flush()
                self._check_write_ownership()
                await session.commit()
                return MutationResult(record=updated, receipt=receipt)
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError(
                    "transaction violates a session constraint", session_id=session_id
                ) from exc
            except BaseException:
                await session.rollback()
                raise

    async def recover_interrupted(self) -> int:
        """Call at application startup only; awaiting_input remains resumable."""
        await self.initialize()
        count = 0
        async with self._lock(), self._sessions() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            try:
                self._check_write_ownership()
                rows = (
                    await session.scalars(
                        select(TrainingSessionRow).where(TrainingSessionRow.active.is_(True))
                    )
                ).all()
                for row in rows:
                    record = await self._record(session, row)
                    self._check_write_ownership()
                    if record.snapshot.state not in {
                        SessionState.CREATED,
                        SessionState.RUNNING,
                        SessionState.CANCELLING,
                    }:
                        continue
                    reviewing = record.snapshot.phase in {SessionPhase.SUPERVISION, SessionPhase.REPORT}
                    report_state = record.snapshot.report_state
                    if (
                        reviewing
                        and record.snapshot.state != SessionState.CANCELLING
                        and report_state != ReportState.CANCELLED
                    ):
                        report_state = ReportState.FAILED
                    target = transition_snapshot(
                        record.snapshot, SessionState.INTERRUPTED, report_state=report_state
                    )
                    draft = EventDraft(
                        round=target.current_round,
                        type=StreamEventType.STATUS_CHANGED,
                        actor=Actor.SYSTEM,
                        source=EventSource.SYSTEM,
                        visibility=EventVisibility.PUBLIC,
                        payload={"reason": "process_interrupted"},
                    )
                    await self._apply(
                        session,
                        row,
                        record,
                        Mutation(snapshot=target, events=[draft], error_code="process_interrupted"),
                    )
                    count += 1
                self._check_write_ownership()
                await session.commit()
                return count
            except BaseException:
                await session.rollback()
                raise

    async def submit_review(self, session_id: str | UUID, submission: Any):
        from .review_store import submit_review

        return await submit_review(self, session_id, submission)

    async def list_reviews(self, session_id: str | UUID) -> list[dict[str, Any]]:
        from .review_store import list_reviews

        return await list_reviews(self, session_id)

    async def list_calibration_samples(self) -> list[dict[str, Any]]:
        from .review_store import list_calibration_samples

        return await list_calibration_samples(self)


__all__ = [
    "TrainingRepository",
    "TrainingRecord",
    "Mutation",
    "MutationResult",
    "EventDraft",
    "StoreError",
    "NotFoundError",
    "ConflictError",
    "BusyError",
    "SchemaVersionError",
    "RuntimeNotOwnedError",
    "SCHEMA_VERSION",
]
