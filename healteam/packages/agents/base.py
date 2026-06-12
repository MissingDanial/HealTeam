"""Base agent contract for HealTeam.

All 6 agents (doctor, psychiatrist, observer, father, mother, patient) MUST
conform to `BaseAgent`. See `docs/architecture.md` §4 for the full rationale.

Design rules baked into the contract:
- Agents see a frozen `SessionContext`, never the raw mutable `Session`.
- `should_speak` MUST be a pure function (no LLM, no side effects).
- `observe` is observer-only; other agents raise `NotImplementedError`.
- Prompt content is loaded from a file path, never hardcoded in Python.
- Construction is LLM-free; the LLM client is injected at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from healteam_memory import (
    Actor,
    Event,
    EventType,
    Session,
)


# ---------------------------------------------------------------------------
# Side-channel data types
# ---------------------------------------------------------------------------


@dataclass
class Hint:
    """A side-channel prompt from the observer agent to the doctor agent.

    Delivered via the earpiece — never seen by the trainee directly.
    """

    round: int
    trigger: str                                  # e.g. "defense_escalation", "stagnation_3_rounds"
    content: str                                  # the prompt text
    confidence: float = 0.5                       # 0.0 .. 1.0
    related_experience: Optional[str] = None      # Experience.pattern_id, if any

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")


@dataclass
class SessionContext:
    """Read-only view that agents see for a single turn.

    The orchestrator constructs a fresh `SessionContext` per turn; agents
    must not retain references to it across turns.
    """

    session: Session                               # full memory snapshot (read-only by convention)
    last_event: Optional[Event] = None             # most recent event, for "what just happened"
    speaker_candidates: list[Actor] = field(default_factory=list)   # who is allowed to speak this turn
    observer_hint: Optional[Hint] = None          # latest hint (only meaningful for doctor)

    # ---- case data (populated by orchestrator.load_case) --------------------
    case_data: Optional[dict[str, Any]] = None     # raw case JSON
    family_profiles: Optional[dict[Actor, dict[str, Any]]] = None  # Actor -> profile dict
    opening_context: Optional[dict[str, Any]] = None  # scene, atmosphere, emotional states
    defense_patterns: Optional[dict[Actor, list[dict[str, Any]]]] = None  # Actor -> defense list
    agent_role_hints: Optional[dict[str, dict[str, Any]]] = None  # agent name -> hints
    milestones: Optional[list[dict[str, Any]]] = None  # expected milestones
    treatment_focus: Optional[list[dict[str, Any]]] = None  # treatment focus areas
    school_template: Optional[str] = None          # school-specific .md content
    family_pattern: Optional[dict[str, Any]] = None  # structural/Bowen/Satir analysis

    # ---- intra-round context (populated by orchestrator) -------------------
    round_events: list[Event] = field(default_factory=list)  # events in current round only
    current_speaker: Optional[Actor] = None        # who is speaking this turn
    emotions: Optional[Any] = None                 # current EmotionMap snapshot
    relationships: Optional[Any] = None            # current RelationshipMap snapshot


# ---------------------------------------------------------------------------
# LLM client interface (so the W1 skeleton has no hard dep on openai/anthropic)
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMClient(Protocol):
    """Minimal LLM call surface used by agents.

    A real implementation lives in `apps/api/app/services/llm.py` (W2).
    W1 agents may be constructed without ever calling this.
    """

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> str:
        """Return the assistant's reply text."""
        ...


# ---------------------------------------------------------------------------
# Base agent
# ---------------------------------------------------------------------------


@runtime_checkable
class BaseAgent(Protocol):
    """The contract every HealTeam agent satisfies.

    Concrete agents are expected to be `@dataclass`es (not subclasses) that
    set the three class-level attributes below. This keeps the contract
    structural and easy to fake in tests.
    """

    # ---- identity (set on the concrete class) -------------------------------

    name: str                  # e.g. "doctor"
    role: str                  # e.g. "AI 主治医师"
    system_prompt_path: str    # absolute or repo-relative path to a .md file

    # ---- runtime wiring (injected by the orchestrator / API layer) ----------

    llm: Optional[LLMClient]
    """The LLM client. May be `None` for W1 skeleton agents that only return
    placeholder text. The orchestrator is responsible for setting this before
    calling `respond`."""

    # ---- required behavior --------------------------------------------------

    def respond(self, context: SessionContext) -> str:
        """Generate the agent's in-character reply for this turn.

        MUST be safe to call when `self.llm is None` — in that case the agent
        should return a deterministic placeholder so the W1 smoke test can
        still compile and run the graph.
        """
        ...

    def should_speak(self, context: SessionContext) -> bool:
        """Decide whether this agent should speak this turn.

        MUST be a pure function of `context`. MUST NOT call the LLM.
        """
        ...

    def observe(self, context: SessionContext) -> Optional[Hint]:
        """Observer-only: emit a side-channel hint, or None to stay silent.

        Other agents raise `NotImplementedError`. The default implementation
        in this Protocol does raise, so non-observer agents automatically
        fail loudly if the orchestrator accidentally routes an `observe()`
        call to them.
        """
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Default repo-relative prompts directory. The agent-prompts task populates
# it; W1 leaves it as a stub directory.
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


def resolve_prompt_path(path: str) -> Path:
    """Resolve a `system_prompt_path` to an absolute `Path`.

    Accepts:
    - absolute paths (returned as-is),
    - paths relative to the `packages/prompts/` directory.
    """
    p = Path(path)
    if p.is_absolute():
        return p
    return (DEFAULT_PROMPTS_DIR / p).resolve()


def load_system_prompt(path: str) -> str:
    """Read the system prompt text from disk.

    Returns an empty string if the file does not exist (W1 case) — agents
    should not crash on missing prompts; the agent-prompts task will fill
    them in.
    """
    p = resolve_prompt_path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def make_placeholder_utterance(actor: Actor, ctx: SessionContext) -> str:
    """Deterministic placeholder used when `self.llm is None` (W1 skeleton)."""
    rnd = ctx.session.round
    return f"[{actor.value} placeholder | round={rnd}]"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


__all__ = [
    "Hint",
    "SessionContext",
    "LLMClient",
    "BaseAgent",
    "DEFAULT_PROMPTS_DIR",
    "resolve_prompt_path",
    "load_system_prompt",
    "make_placeholder_utterance",
]
