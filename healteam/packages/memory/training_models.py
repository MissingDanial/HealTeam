"""SQLite schema v2; expert reviews append without rewriting session results."""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TrainingBase(DeclarativeBase):
    pass


class TrainingSessionRow(TrainingBase):
    __tablename__ = "training_sessions"
    __table_args__ = (
        Index("uq_training_active_owner", "owner", unique=True, sqlite_where=text("active = 1")),
        CheckConstraint("completed_rounds >= 0", name="ck_completed_rounds_nonnegative"),
        CheckConstraint("revision >= 0", name="ck_session_revision_nonnegative"),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(32), nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    case_json: Mapped[str] = mapped_column(Text, nullable=False)
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    completed_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TrainingEventRow(TrainingBase):
    __tablename__ = "training_events"
    __table_args__ = (CheckConstraint("seq >= 1", name="ck_event_sequence_positive"),)

    session_id: Mapped[str] = mapped_column(
        ForeignKey("training_sessions.session_id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    envelope_json: Mapped[str] = mapped_column(Text, nullable=False)


class TrainingReceiptRow(TrainingBase):
    __tablename__ = "training_receipts"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("training_sessions.session_id", ondelete="CASCADE"), primary_key=True
    )
    receipt_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)


class ExpertReviewRow(TrainingBase):
    __tablename__ = "training_expert_reviews"
    __table_args__ = (
        Index("uq_review_session_revision", "session_id", "revision", unique=True),
        Index("uq_review_client_request", "session_id", "client_review_id", unique=True),
        CheckConstraint("revision >= 1", name="ck_review_revision_positive"),
    )

    review_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("training_sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    client_review_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rubric_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    submission_json: Mapped[str] = mapped_column(Text, nullable=False)
    review_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
