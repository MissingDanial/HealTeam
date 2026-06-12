"""Memory store interface for HealTeam.

W1 ships an **in-memory** implementation only. The Postgres + pgvector
implementation is the W2 `persistence` task's job; it must conform to the
`MemoryStore` Protocol defined here.

Why an interface from day one:
- Lets smoke tests run without a database.
- Lets the W2 persistence task be a drop-in replacement.
- Lets us swap to a fake in unit tests without monkey-patching.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import uuid4

from .schema import Experience, Session


@runtime_checkable
class MemoryStore(Protocol):
    """Abstract store for `Session` and `Experience` records.

    Implementations: `InMemoryStore` (W1), `PgVectorStore` (W2+).
    """

    # --- sessions --------------------------------------------------------

    async def create_session(self, session: Session) -> Session:
        """Persist a new session. Returns the stored (possibly id-stamped) record."""
        ...

    async def get_session(self, session_id: str) -> Session | None:
        """Fetch a session by id, or None if not found."""
        ...

    async def update_session(self, session: Session) -> Session:
        """Replace/update an existing session. Implementations should bump `updated_at`."""
        ...

    async def list_sessions(self, case_id: str | None = None) -> list[Session]:
        """List sessions, optionally filtered by `case_id`. Order: most recent first."""
        ...

    # --- experience library ---------------------------------------------

    async def upsert_experience(self, exp: Experience) -> Experience:
        """Insert or update an experience record (key: `pattern_id`)."""
        ...

    async def get_experience(self, pattern_id: str) -> Experience | None:
        ...

    async def list_experience(self, school: str | None = None) -> list[Experience]:
        """List experience entries, optionally filtered by school. Order: highest weight first."""
        ...


class InMemoryStore:
    """Pure-Python, in-process implementation of `MemoryStore`.

    Safe for tests, smoke runs, and local dev. NOT safe for multi-process
    deployments — use `PgVectorStore` for that.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._experience: dict[str, Experience] = {}

    # --- sessions --------------------------------------------------------

    async def create_session(self, session: Session) -> Session:
        if not session.id:
            session.id = str(uuid4())
        if session.id in self._sessions:
            raise ValueError(f"session {session.id} already exists")
        self._sessions[session.id] = session.model_copy(deep=True)
        return self._sessions[session.id].model_copy(deep=True)

    async def get_session(self, session_id: str) -> Session | None:
        s = self._sessions.get(session_id)
        return s.model_copy(deep=True) if s else None

    async def update_session(self, session: Session) -> Session:
        if session.id not in self._sessions:
            raise KeyError(f"session {session.id} not found")
        self._sessions[session.id] = session.model_copy(deep=True)
        return self._sessions[session.id].model_copy(deep=True)

    async def list_sessions(self, case_id: str | None = None) -> list[Session]:
        items = list(self._sessions.values())
        if case_id is not None:
            items = [s for s in items if s.case_id == case_id]
        items.sort(key=lambda s: s.created_at, reverse=True)
        return [s.model_copy(deep=True) for s in items]

    # --- experience library ---------------------------------------------

    async def upsert_experience(self, exp: Experience) -> Experience:
        self._experience[exp.pattern_id] = exp.model_copy(deep=True)
        return self._experience[exp.pattern_id].model_copy(deep=True)

    async def get_experience(self, pattern_id: str) -> Experience | None:
        e = self._experience.get(pattern_id)
        return e.model_copy(deep=True) if e else None

    async def list_experience(self, school: str | None = None) -> list[Experience]:
        items = list(self._experience.values())
        if school is not None:
            items = [e for e in items if e.school.value == school]
        items.sort(key=lambda e: e.weight, reverse=True)
        return [e.model_copy(deep=True) for e in items]


__all__ = ["MemoryStore", "InMemoryStore"]
