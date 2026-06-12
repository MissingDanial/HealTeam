"""Shared memory schema for HealTeam.

This module is the **single source of truth** for the shape of session state.
All other modules (agents, orchestrator, API, persistence) MUST import from here.

See `docs/architecture.md` §3 for the field reference and rationale.

NOTE: W1 ships schema only. Persistence (Postgres + pgvector) is a later task;
this module has no DB dependency and is safe to import anywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums (locked vocabulary — see architecture.md §3.3)
# ---------------------------------------------------------------------------


class School(str, Enum):
    """Therapeutic school (流派). Locked at session start."""

    STRUCTURAL = "structural"   # 结构派 (Minuchin)
    BOWEN = "bowen"             # Bowen 系统式
    SATIR = "satir"             # 萨提亚


class SessionStatus(str, Enum):
    """Lifecycle of a therapy session."""

    PENDING = "pending"     # created, not yet started
    RUNNING = "running"     # therapy in progress
    ENDED = "ended"         # therapy ended, supervision not yet run
    REPORTED = "reported"   # supervision done, report delivered


class Actor(str, Enum):
    """Who emitted an event or who is allowed to speak.

    Note: `trainee` is the human learner playing the therapist role.
    """

    DOCTOR = "doctor"               # AI 主治医师
    PSYCHIATRIST = "psychiatrist"   # AI 心理医生
    OBSERVER = "observer"           # AI 旁观者
    FATHER = "father"               # AI 父亲
    MOTHER = "mother"               # AI 母亲
    PATIENT = "patient"             # AI 病人 (e.g. 青少年)
    TRAINEE = "trainee"             # the human learner
    SYSTEM = "system"               # orchestrator / framework messages


class EventType(str, Enum):
    """Category of a logged event."""

    UTTERANCE = "utterance"     # something said aloud in the therapy room
    DEFENSE = "defense"         # a defense mechanism observed (rationalization, denial, ...)
    INTERVENTION = "intervention"  # a therapeutic technique applied
    HINT = "hint"               # observer → doctor side-channel prompt
    SYSTEM = "system"           # orchestrator events (round start, end, ...)
    META = "meta"               # anything that doesn't fit above (tags-only, no content)


class RelationType(str, Enum):
    """Pair-wise relationship between two family actors."""

    ALLIANCE = "alliance"
    CONFRONTATION = "confrontation"
    NEUTRAL = "neutral"
    COALITION = "coalition"


class Effectiveness(str, Enum):
    """Whether an intervention moved the therapy forward."""

    PENDING = "pending"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class Event(BaseModel):
    """A single observable happening in the therapy room.

    Events are append-only. Once written, an event's `round`, `actor`, and
    `type` MUST NOT change. (You can append a follow-up `meta` event to
    "correct" something.)
    """

    model_config = ConfigDict(extra="forbid")

    round: int = Field(ge=0, description="Round at which the event was emitted")
    actor: Actor
    type: EventType
    content: str = Field(min_length=0, max_length=8000)
    tags: list[str] = Field(default_factory=list, description="Free-form tags, e.g. ['rationalization']")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("tags")
    @classmethod
    def _tags_are_non_empty_strings(cls, v: list[str]) -> list[str]:
        for t in v:
            if not isinstance(t, str) or not t.strip():
                raise ValueError("tags must be non-empty strings")
        return v


class Intervention(BaseModel):
    """A therapeutic technique applied by someone (trainee or doctor agent)."""

    model_config = ConfigDict(extra="forbid")

    round: int = Field(ge=0)
    by: Actor
    type: str = Field(min_length=1, max_length=64, description="e.g. 'reframing', 'genogram', 'circular_question'")
    effectiveness: Effectiveness = Effectiveness.PENDING
    notes: str = Field(default="", max_length=2000)


class EmotionMap(BaseModel):
    """actor -> emotion_label snapshot.

    Stored as a dict for fast lookup. Keys must be valid `Actor` values.
    """

    model_config = ConfigDict(extra="forbid")

    emotions: dict[Actor, str] = Field(default_factory=dict)

    def get(self, actor: Actor) -> Optional[str]:
        return self.emotions.get(actor)

    def set(self, actor: Actor, label: str) -> None:
        self.emotions[actor] = label

    @field_validator("emotions")
    @classmethod
    def _labels_non_empty(cls, v: dict[Actor, str]) -> dict[Actor, str]:
        for actor, label in v.items():
            if not label or not label.strip():
                raise ValueError(f"emotion label for {actor} must be non-empty")
        return v


class RelationshipMap(BaseModel):
    """pair -> RelationType snapshot.

    Pair keys MUST be of the form `"{actor_a}__{actor_b}"` with the two actors
    sorted alphabetically. This canonicalization makes "father-mother" and
    "mother-father" the same key.
    """

    model_config = ConfigDict(extra="forbid")

    relationships: dict[str, RelationType] = Field(default_factory=dict)

    @staticmethod
    def pair_key(a: Actor, b: Actor) -> str:
        if a == b:
            raise ValueError("cannot relate an actor to itself")
        x, y = sorted([a.value, b.value])
        return f"{x}__{y}"

    def get(self, a: Actor, b: Actor) -> Optional[RelationType]:
        return self.relationships.get(self.pair_key(a, b))

    def set(self, a: Actor, b: Actor, rel: RelationType) -> None:
        self.relationships[self.pair_key(a, b)] = rel

    @field_validator("relationships")
    @classmethod
    def _keys_canonical(cls, v: dict[str, RelationType]) -> dict[str, RelationType]:
        for k in v.keys():
            if "__" not in k:
                raise ValueError(
                    f"relationship key '{k}' must be of the form 'actor_a__actor_b' (sorted)"
                )
        return v


class Experience(BaseModel):
    """A piece of accumulated wisdom from past sessions.

    Lives in the experience library (Postgres + pgvector in W2+). In W1 it's
    a pure-Python dataclass-shaped Pydantic model so smoke tests can construct
    one without a DB.
    """

    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(min_length=1, max_length=128)
    school: School
    condition: str = Field(min_length=1, max_length=500, description="When this pattern applies")
    hint: str = Field(min_length=1, max_length=2000)
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    embedding: Optional[list[float]] = Field(
        default=None,
        description="pgvector embedding for semantic retrieval (populated in W2+)",
    )


class Session(BaseModel):
    """A single therapy session, from case load to report delivery.

    This is the unit of work. A session belongs to one case and one school.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str = Field(min_length=1, max_length=128)
    school: School
    round: int = Field(default=0, ge=0)
    status: SessionStatus = SessionStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    events: list[Event] = Field(default_factory=list)
    emotions: EmotionMap = Field(default_factory=EmotionMap)
    relationships: RelationshipMap = Field(default_factory=RelationshipMap)
    interventions: list[Intervention] = Field(default_factory=list)
    experience_refs: list[str] = Field(default_factory=list, description="Experience.pattern_id references")

    # ---- convenience methods (do not change shape) ----------------------------

    def append_event(self, event: Event) -> None:
        """Append an event and bump `updated_at` and (if needed) `round`."""
        self.events.append(event)
        self.updated_at = datetime.now(timezone.utc)
        if event.round > self.round:
            self.round = event.round

    def snapshot(self) -> "Session":
        """Return a deep copy (Pydantic v2 `model_copy(deep=True)`)."""
        return self.model_copy(deep=True)


# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------


__all__ = [
    "School",
    "SessionStatus",
    "Actor",
    "EventType",
    "RelationType",
    "Effectiveness",
    "Event",
    "Intervention",
    "EmotionMap",
    "RelationshipMap",
    "Experience",
    "Session",
]
