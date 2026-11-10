"""LangGraph orchestrator for HealTeam.

Multi-party dialogue orchestrator that:
- Loads case data (family profiles, defense patterns, milestones, school templates).
- Runs inner-round loops with dynamic speaker selection based on context.
- Tracks milestone achievement and defense escalation.
- Delivers observer hints to the doctor via side-channel.

Graph topology::

    load_case -> round_init -> decide_speaker -> agent_respond -> observer_hint
                                                      ^                     |
                                                      |    continue_round?  |
                                                      |--- (yes) -----------|
                                                      |    (no)
                                                      v
                                                 update_memory -> check_end -> round_init (loop)
                                                                      |-> END
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from healteam_memory import (
    Actor,
    Event,
    EventType,
    School,
    Session,
    SessionStatus,
)

from .base import BaseAgent, Hint, SessionContext
from .case_loader import build_case_context
from .doctor import DoctorAgent
from .father import FatherAgent
from .mother import MotherAgent
from .observer import ObserverAgent
from .patient import PatientAgent
from .psychiatrist import PsychiatristAgent

logger = logging.getLogger("healteam.agents.orchestrator")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Hard cap on rounds. PRD §11.1 says 15-25; we pick 20 as the default.
DEFAULT_MAX_ROUNDS = 20

#: Max speakers in a single round (prevents infinite inner loops).
MAX_SPEAKERS_PER_ROUND = 6

#: Actors that participate in the therapy dialogue.
SPEAKING_ACTORS: tuple[Actor, ...] = (
    Actor.DOCTOR,
    Actor.FATHER,
    Actor.MOTHER,
    Actor.PATIENT,
)

# Family-only actors (no doctor).
FAMILY_ACTORS: tuple[Actor, ...] = (
    Actor.FATHER,
    Actor.MOTHER,
    Actor.PATIENT,
)


def _pick_next_speaker(state: TherapyState, ctx: SessionContext, agents: dict[Actor, BaseAgent]) -> Optional[Actor]:
    """Dynamic speaker selection based on conversation context.

    Instead of a fixed round plan, this function evaluates who should speak next
    based on: who was just addressed, emotional dynamics, defense patterns,
    round number, and controlled randomness.

    Returns the Actor to speak, or None to end the round.
    """
    speakers_this_round = state.get("speakers_this_round", [])
    rnd = state.get("round", 0)
    last_event = state.get("last_event")
    round_events = ctx.round_events or []

    # --- Early termination checks ---
    # Round must have at least 2 speakers (doctor + 1 family).
    if len(speakers_this_round) < 2:
        # If only doctor spoke so far, pick a family member.
        if len(speakers_this_round) == 1 and speakers_this_round[0] == Actor.DOCTOR:
            return _pick_family_responder(ctx, agents, rnd, last_event)
        # If no one spoke yet, doctor opens.
        if not speakers_this_round:
            return Actor.DOCTOR

    # After doctor's second utterance, high chance to end the round.
    doctor_count = sum(1 for s in speakers_this_round if s == Actor.DOCTOR)
    if doctor_count >= 2:
        # 80% chance to end the round after doctor's second turn.
        if random.random() < 0.8:
            return None

    # Safety cap.
    if len(speakers_this_round) >= state.get("max_speakers_per_round", MAX_SPEAKERS_PER_ROUND):
        return None

    # --- Dynamic speaker selection ---
    # If doctor hasn't spoken yet this round, doctor opens.
    if Actor.DOCTOR not in speakers_this_round:
        return Actor.DOCTOR

    # If doctor spoke once and round is getting long, sometimes doctor closes.
    if doctor_count == 1 and len(speakers_this_round) >= 3:
        if random.random() < 0.4:
            return Actor.DOCTOR

    # Otherwise, pick a family member based on context.
    return _pick_family_responder(ctx, agents, rnd, last_event)


def _pick_family_responder(ctx: SessionContext, agents: dict[Actor, BaseAgent], rnd: int, last_event: Optional[Event]) -> Optional[Actor]:
    """Pick which family member should respond based on context and probability.

    Uses weighted random selection influenced by:
    - Who was directly addressed in the last utterance
    - Emotional triggers in recent dialogue
    - Round number (emotional arc)
    - Defense mechanism patterns
    """
    candidates: list[Actor] = []
    weights: list[float] = []

    last_content = last_event.content.lower() if last_event and last_event.content else ""
    round_events = ctx.round_events or []
    already_spoke = {e.actor for e in round_events}

    # --- Father ---
    father_base = 0.3  # Base probability
    # Boost if directly addressed.
    if any(kw in last_content for kw in ["爸爸", "父亲", "丈夫", "建国"]):
        father_base = 0.85
    # Boost if mother just criticized him (conflict escalation).
    mother_attacks = [e for e in round_events if e.actor == Actor.MOTHER and
                      any(kw in e.content for kw in ["他能", "心硬", "不管", "不问", "不关心"])]
    if mother_attacks:
        father_base = max(father_base, 0.7)
    # Lower in early rounds (father tends to withdraw).
    if rnd < 3:
        father_base *= 0.6
    # Lower if father already spoke this round.
    if Actor.FATHER in already_spoke:
        father_base *= 0.3

    candidates.append(Actor.FATHER)
    weights.append(max(father_base, 0.05))

    # --- Mother ---
    mother_base = 0.35  # Slightly higher base (anxiety-driven).
    # Boost if directly addressed.
    if any(kw in last_content for kw in ["妈妈", "母亲", "秀英", "妻子"]):
        mother_base = 0.85
    # Boost if patient shows distress.
    patient_distress = [e for e in round_events if e.actor == Actor.PATIENT and
                        any(kw in e.content for kw in ["肚子", "不舒服", "不想", "无所谓", "随便"])]
    if patient_distress:
        mother_base = max(mother_base, 0.75)
    # Boost if father said something triggering.
    father_triggers = [e for e in round_events if e.actor == Actor.FATHER and
                       any(kw in e.content for kw in ["惯的", "管教", "不应该", "问题"])]
    if father_triggers:
        mother_base = max(mother_base, 0.7)
    # Mother is very active in mid rounds (anxiety peaks).
    if 3 <= rnd <= 8:
        mother_base *= 1.3
    # Lower if already spoke this round.
    if Actor.MOTHER in already_spoke:
        mother_base *= 0.25

    candidates.append(Actor.MOTHER)
    weights.append(min(mother_base, 1.0))

    # --- Patient ---
    patient_base = 0.15  # Low base (resistant).
    # Boost if directly addressed.
    if any(kw in last_content for kw in ["子轩", "孩子", "同学", "儿子"]):
        patient_base = 0.8
    # Emotional arc: more willing in later rounds.
    if rnd < 3:
        patient_base *= 0.3  # Very resistant early.
    elif rnd < 6:
        patient_base *= 0.7  # Slightly more willing.
    elif rnd < 10:
        patient_base *= 1.2  # Opening up.
    else:
        patient_base *= 1.5  # Breakthrough territory.
    # Boost if mother is over-protecting (patient might react).
    if mother_attacks or patient_distress:
        patient_base = max(patient_base, 0.5)
    # Lower if already spoke this round.
    if Actor.PATIENT in already_spoke:
        patient_base *= 0.2

    candidates.append(Actor.PATIENT)
    weights.append(min(patient_base, 1.0))

    # --- Random variation: sometimes someone "wants to say something" ---
    for i in range(len(candidates)):
        weights[i] += random.uniform(0.0, 0.1)

    # Filter to candidates whose agents say they should speak.
    valid: list[tuple[Actor, float]] = []
    for actor, w in zip(candidates, weights):
        agent = agents.get(actor)
        if agent and agent.should_speak(ctx):
            valid.append((actor, w))

    # Fallback: if no one wants to speak but doctor already spoke,
    # force at least one family member to respond (natural conversation flow).
    if not valid:
        # Prefer family members who haven't spoken yet this round.
        not_yet_spoken = [a for a in candidates if a not in already_spoke]
        if not_yet_spoken:
            # Give patient priority if they haven't spoken and round >= 2.
            if Actor.PATIENT in not_yet_spoken and rnd >= 2:
                if random.random() < 0.5:
                    return Actor.PATIENT
            # Pick randomly among those who haven't spoken.
            return random.choice(not_yet_spoken)
        # Everyone has spoken — pick the one with highest weight.
        best_idx = max(range(len(weights)), key=lambda i: weights[i])
        return candidates[best_idx]

    # Weighted random selection.
    total = sum(w for _, w in valid)
    if total <= 0:
        return random.choice([a for a, _ in valid])

    r = random.uniform(0, total)
    cumulative = 0.0
    for actor, w in valid:
        cumulative += w
        if r <= cumulative:
            return actor

    return valid[-1][0]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class TherapyState(TypedDict, total=False):
    """LangGraph state container for one therapy run.

    Critical fields use ``Required[]`` so type checkers catch missing keys.
    Other fields are optional so node functions can return partial updates.
    Keys are part of the **public contract** — see ``docs/architecture.md`` §5.1.
    """

    # ---- core (inherited from W1) ------------------------------------------
    session: Session
    last_event: Optional[Event]
    pending_speaker: Optional[Actor]
    observer_hint: Optional[Hint]
    round: int
    is_terminated: bool
    termination_reason: Optional[str]

    # ---- multi-party round tracking ----------------------------------------
    speakers_this_round: list[Actor]
    max_speakers_per_round: int
    doctor_spoke_this_round: bool
    round_active: bool  # True while the inner round loop should continue.

    # ---- case context (populated by load_case) -----------------------------
    case_context: Optional[dict[str, Any]]

    # ---- tracking state (populated by update_memory) -----------------------
    milestones_achieved: list[str]
    milestones_remaining: list[dict[str, Any]]
    hint_history: list[dict[str, Any]]


def _empty_state(session: Session) -> TherapyState:
    return TherapyState(
        session=session,
        last_event=None,
        pending_speaker=None,
        observer_hint=None,
        round=session.round,
        is_terminated=False,
        termination_reason=None,
        speakers_this_round=[],
        max_speakers_per_round=MAX_SPEAKERS_PER_ROUND,
        doctor_spoke_this_round=False,
        round_active=True,
        case_context=None,
        milestones_achieved=[],
        milestones_remaining=[],
        hint_history=[],
    )


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------


def _make_node_load_case(orchestrator: "Orchestrator"):
    def load_case(state: TherapyState) -> TherapyState:
        session: Session = state["session"]
        session.status = SessionStatus.RUNNING

        # Load case data if not already loaded.
        if state.get("case_context") is not None:
            return state

        case_id = orchestrator.case_id or session.case_id
        school = orchestrator.school or session.school
        if case_id != session.case_id or school != session.school:
            raise ValueError("Orchestrator configuration must match the session case and school")
        # Bad content must fail before the first utterance, not after a whole
        # session of model calls without a case.
        ctx = build_case_context(case_id, school)

        # Initialize milestone tracking.
        raw_milestones = ctx.get("milestones", [])
        milestones_remaining = [
            m for m in raw_milestones if m.get("id")
        ]

        return {
            **state,
            "case_context": ctx,
            "milestones_remaining": milestones_remaining,
            "milestones_achieved": [],
        }

    return load_case


def _make_node_round_init(orchestrator: "Orchestrator"):
    def round_init(state: TherapyState) -> TherapyState:
        # state.round counts completed rounds at this boundary. Publish the
        # next one to the session BEFORE any agent sees the turn context.
        session: Session = state["session"]
        session.round = state.get("round", 0) + 1
        return {
            **state,
            "round": session.round,
            "speakers_this_round": [],
            "doctor_spoke_this_round": False,
            "round_active": True,
            "pending_speaker": None,
        }

    return round_init


def _build_session_context(state: TherapyState, session: Session) -> SessionContext:
    """Build a SessionContext from the current state (shared by decide_speaker and agent_respond)."""
    return SessionContext(
        session=session,
        last_event=state.get("last_event"),
        speaker_candidates=list(SPEAKING_ACTORS),
        observer_hint=state.get("observer_hint"),
        case_data=state.get("case_context", {}).get("case") if state.get("case_context") else None,
        family_profiles=state.get("case_context", {}).get("family_profiles") if state.get("case_context") else None,
        opening_context=state.get("case_context", {}).get("opening_context") if state.get("case_context") else None,
        defense_patterns=state.get("case_context", {}).get("defense_patterns") if state.get("case_context") else None,
        agent_role_hints=state.get("case_context", {}).get("agent_hints") if state.get("case_context") else None,
        milestones=state.get("case_context", {}).get("milestones") if state.get("case_context") else None,
        treatment_focus=state.get("case_context", {}).get("treatment_focus") if state.get("case_context") else None,
        school_template=state.get("case_context", {}).get("school_template") if state.get("case_context") else None,
        family_pattern=state.get("case_context", {}).get("family_pattern") if state.get("case_context") else None,
        round_events=[e for e in session.events if e.round == session.round],
    )


def _make_node_decide_speaker(orchestrator: "Orchestrator"):
    def decide_speaker(state: TherapyState) -> TherapyState:
        session: Session = state["session"]
        speakers_this_round = state.get("speakers_this_round", [])
        max_speakers = state.get("max_speakers_per_round", MAX_SPEAKERS_PER_ROUND)

        # Safety: cap speakers per round.
        if len(speakers_this_round) >= max_speakers:
            return {**state, "pending_speaker": None, "round_active": False}

        # If nobody spoke yet, always pick doctor to open.
        if not speakers_this_round:
            return {**state, "pending_speaker": Actor.DOCTOR}

        # If only doctor spoke, always pick a family member to respond.
        if len(speakers_this_round) == 1 and speakers_this_round[0] == Actor.DOCTOR:
            ctx = _build_session_context(state, session)
            speaker = _pick_family_responder(ctx, orchestrator.agents, state.get("round", 0), state.get("last_event"))
            return {**state, "pending_speaker": speaker or Actor.MOTHER}

        # For subsequent speakers, use dynamic selection with round-end logic.
        ctx = _build_session_context(state, session)
        speaker = _pick_next_speaker(state, ctx, orchestrator.agents)

        if speaker is None:
            # Dynamic selection decided to end the round.
            return {**state, "pending_speaker": None, "round_active": False}

        return {**state, "pending_speaker": speaker}

    return decide_speaker


def _make_node_agent_respond(orchestrator: "Orchestrator"):
    def agent_respond(state: TherapyState) -> TherapyState:
        session: Session = state["session"]
        speaker: Optional[Actor] = state.get("pending_speaker")
        if speaker is None:
            return state

        agent = orchestrator.agents.get(speaker)
        if agent is None:
            return {**state, "pending_speaker": None}

        ctx = _build_session_context(state, session)
        # Add current_speaker to context.
        ctx = SessionContext(
            session=ctx.session,
            last_event=ctx.last_event,
            speaker_candidates=ctx.speaker_candidates,
            observer_hint=ctx.observer_hint,
            case_data=ctx.case_data,
            family_profiles=ctx.family_profiles,
            opening_context=ctx.opening_context,
            defense_patterns=ctx.defense_patterns,
            agent_role_hints=ctx.agent_role_hints,
            milestones=ctx.milestones,
            treatment_focus=ctx.treatment_focus,
            school_template=ctx.school_template,
            family_pattern=ctx.family_pattern,
            round_events=ctx.round_events,
            current_speaker=speaker,
        )

        # A live model failure is a failed run, not a successful utterance.
        # Explicit no-model demos are handled by each agent before this call.
        text = agent.respond(ctx)

        # Append utterance event.
        evt = Event(
            round=session.round,
            actor=speaker,
            type=EventType.UTTERANCE,
            content=text,
            tags=[],
        )
        session.append_event(evt)

        # Track speaker and doctor status.
        speakers_this_round = list(state.get("speakers_this_round", []))
        speakers_this_round.append(speaker)
        doctor_spoke = state.get("doctor_spoke_this_round", False) or speaker == Actor.DOCTOR

        return {
            **state,
            "last_event": evt,
            "pending_speaker": None,
            "speakers_this_round": speakers_this_round,
            "doctor_spoke_this_round": doctor_spoke,
        }

    return agent_respond


def _make_node_observer_hint(orchestrator: "Orchestrator"):
    def observer_hint(state: TherapyState) -> TherapyState:
        session: Session = state["session"]
        observer = orchestrator.agents.get(Actor.OBSERVER)
        if observer is None:
            return {**state, "observer_hint": None}

        ctx = _build_session_context(state, session)
        # Override observer_hint to None for the observer itself.
        ctx = SessionContext(
            session=ctx.session,
            last_event=ctx.last_event,
            speaker_candidates=ctx.speaker_candidates,
            observer_hint=None,
            case_data=ctx.case_data,
            family_profiles=ctx.family_profiles,
            opening_context=ctx.opening_context,
            defense_patterns=ctx.defense_patterns,
            agent_role_hints=ctx.agent_role_hints,
            milestones=ctx.milestones,
            treatment_focus=ctx.treatment_focus,
            school_template=ctx.school_template,
            family_pattern=ctx.family_pattern,
            round_events=ctx.round_events,
        )

        try:
            hint = observer.observe(ctx)
        except Exception:
            logger.exception("observer.observe() failed, skipping hint")
            hint = None

        if hint is not None:
            # Log the hint as an event so it shows up in the session log.
            session.append_event(
                Event(
                    round=session.round,
                    actor=Actor.OBSERVER,
                    type=EventType.HINT,
                    content=hint.content,
                    tags=[hint.trigger],
                )
            )
            # Track hint history.
            hint_history = list(state.get("hint_history", []))
            hint_history.append({
                "round": session.round,
                "trigger": hint.trigger,
                "content": hint.content,
                "confidence": hint.confidence,
            })
            return {**state, "observer_hint": hint, "hint_history": hint_history}

        return {**state, "observer_hint": hint}

    return observer_hint


def _make_node_update_memory(orchestrator: "Orchestrator"):
    def update_memory(state: TherapyState) -> TherapyState:
        # The round was assigned at round_init; do not advance a second copy.
        new_round = state["session"].round

        # Milestone tracking: check if any milestones were achieved this round.
        # (Full LLM-based detection is Phase 3; W2 does keyword-based stub.)
        milestones_remaining = list(state.get("milestones_remaining", []))
        milestones_achieved = list(state.get("milestones_achieved", []))

        # Simple keyword-based milestone detection stub.
        session: Session = state["session"]
        recent_events = [e for e in session.events if e.round == session.round]
        recent_text = " ".join(e.content for e in recent_events if e.content)

        newly_achieved: list[str] = []
        for m in list(milestones_remaining):
            signals = m.get("validation_signals", [])
            for sig in signals:
                if sig.lower() in recent_text.lower():
                    milestones_achieved.append(m["id"])
                    newly_achieved.append(m["id"])
                    milestones_remaining = [r for r in milestones_remaining if r.get("id") != m["id"]]
                    logger.info("Milestone %s achieved in round %d", m["id"], session.round)
                    break

        return {
            **state,
            "round": new_round,
            "milestones_achieved": milestones_achieved,
            "milestones_remaining": milestones_remaining,
        }

    return update_memory


def _make_node_check_end(orchestrator: "Orchestrator"):
    def check_end(state: TherapyState) -> TherapyState:
        session: Session = state["session"]

        # Check max rounds.
        if state.get("round", 0) >= orchestrator.max_rounds:
            session.status = SessionStatus.ENDED
            return {
                **state,
                "is_terminated": True,
                "termination_reason": "max_rounds",
            }

        # Check if all milestones achieved (early termination).
        if state.get("milestones_remaining") == [] and state.get("milestones_achieved"):
            session.status = SessionStatus.ENDED
            return {
                **state,
                "is_terminated": True,
                "termination_reason": "all_milestones_achieved",
            }

        return state

    return check_end


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------


def _route_after_check(state: TherapyState) -> str:
    if state.get("is_terminated"):
        return "end"
    return "round_init"


def _route_continue_round(state: TherapyState) -> str:
    """After observer_hint, decide whether to continue the inner round loop.

    End the round when round_active is False (set by decide_speaker when
    the dynamic selection decides the round is over or max speakers reached).
    """
    if not state.get("round_active", True):
        return "update_memory"

    # Continue: round is still active.
    return "decide_speaker"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class Orchestrator:
    """The multi-party therapy orchestrator (LangGraph-backed)."""

    max_rounds: int = DEFAULT_MAX_ROUNDS
    case_id: Optional[str] = None
    school: Optional[School] = None
    agents: dict[Actor, BaseAgent] = field(default_factory=dict)
    _graph: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.max_rounds) is not int or not 1 <= self.max_rounds <= 30:
            raise ValueError("max_rounds must be an integer from 1 to 30")
        # Default 6 agents. Callers may override by setting ``self.agents`` after
        # construction (e.g. to inject fakes in tests).
        if not self.agents:
            self.agents = {
                Actor.DOCTOR: DoctorAgent(),
                Actor.PSYCHIATRIST: PsychiatristAgent(),
                Actor.OBSERVER: ObserverAgent(),
                Actor.FATHER: FatherAgent(),
                Actor.MOTHER: MotherAgent(),
                Actor.PATIENT: PatientAgent(),
            }
        # Build the graph once.
        self._graph = self._build_graph()

    # ---- graph construction ----------------------------------------------

    def _build_graph(self):
        g = StateGraph(TherapyState)

        # Nodes.
        g.add_node("load_case", _make_node_load_case(self))
        g.add_node("round_init", _make_node_round_init(self))
        g.add_node("decide_speaker", _make_node_decide_speaker(self))
        g.add_node("agent_respond", _make_node_agent_respond(self))
        g.add_node("observer_hint", _make_node_observer_hint(self))
        g.add_node("update_memory", _make_node_update_memory(self))
        g.add_node("check_end", _make_node_check_end(self))

        # Edges.
        g.set_entry_point("load_case")
        g.add_edge("load_case", "round_init")
        g.add_edge("round_init", "decide_speaker")
        g.add_edge("decide_speaker", "agent_respond")
        g.add_edge("agent_respond", "observer_hint")

        # Inner round loop: continue or end round?
        g.add_conditional_edges(
            "observer_hint",
            _route_continue_round,
            {
                "decide_speaker": "decide_speaker",
                "update_memory": "update_memory",
            },
        )

        g.add_edge("update_memory", "check_end")

        # Outer loop: continue or end session?
        g.add_conditional_edges(
            "check_end",
            _route_after_check,
            {
                "round_init": "round_init",
                "end": END,
            },
        )

        return g.compile()

    # ---- public API -------------------------------------------------------

    def run(self, session: Session) -> Session:
        """Drive a session to termination (or until ``max_rounds``).

        Synchronous wrapper around ``arun()``. Use ``arun()`` directly in async contexts.
        Returns the (mutated) session.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already inside an event loop — run synchronously via invoke.
            state = _empty_state(session)
            final: TherapyState = self._graph.invoke(state, config=self._run_config())
            session.round = int(final.get("round", session.round))
            return session

        return asyncio.run(self.arun(session))

    async def arun(self, session: Session) -> Session:
        """Async version: drive a session to termination (or until ``max_rounds``).

        Use this in async contexts (FastAPI, WebSocket handlers).
        Returns the (mutated) session.
        """
        state = _empty_state(session)
        final: TherapyState = await self._graph.ainvoke(state, config=self._run_config())
        session.round = int(final.get("round", session.round))
        return session

    def step(self, session: Session) -> TherapyState:
        """Run a single turn (for testing / WebSocket streaming).

        Invokes the graph with a hard stop after one ``check_end`` cycle.
        """
        state = _empty_state(session)
        return self._graph.invoke(state, config=self._run_config())

    def _run_config(self) -> dict[str, int]:
        # One room utterance visits three nodes. Include the final no-speaker
        # pass and outer-round nodes, with margin, independently of whichever
        # LangGraph default a fresh environment happens to install.
        return {"recursion_limit": self.max_rounds * (3 * MAX_SPEAKERS_PER_ROUND + 8) + 16}


__all__ = [
    "TherapyState",
    "Orchestrator",
    "DEFAULT_MAX_ROUNDS",
    "MAX_SPEAKERS_PER_ROUND",
    "SPEAKING_ACTORS",
    "FAMILY_ACTORS",
]
