"""Observable S0 invariants, exercised without paid model calls."""

from __future__ import annotations

import random

import pytest
from healteam_agents import (
    FatherAgent,
    MotherAgent,
    ObserverAgent,
    Orchestrator,
    PatientAgent,
    SessionContext,
)
from healteam_memory import Actor, Event, EventType, School, Session


def test_first_round_is_numbered_one_in_every_emitted_event() -> None:
    random.seed(1)
    session = Session(case_id="teen-truancy-01", school=School.STRUCTURAL)
    Orchestrator(max_rounds=1).run(session)
    assert session.round == 1
    assert session.events
    assert {event.round for event in session.events} == {1}


@pytest.mark.parametrize("rounds", [10, 20, 30])
def test_long_runs_have_consistent_event_rounds_and_an_explicit_graph_budget(rounds: int) -> None:
    random.seed(1)
    session = Session(case_id="teen-truancy-01", school=School.SATIR)
    Orchestrator(max_rounds=rounds).run(session)
    assert session.round == rounds
    utterances = [event for event in session.events if event.type == EventType.UTTERANCE]
    assert {event.round for event in utterances} == set(range(1, rounds + 1))
    assert [event.round for event in session.events] == sorted(event.round for event in session.events)


@pytest.mark.parametrize("rounds", [0, -1, 31, True])
def test_orchestrator_rejects_out_of_range_round_limits(rounds: int) -> None:
    with pytest.raises(ValueError):
        Orchestrator(max_rounds=rounds)


class RecordingLLM:
    def __init__(self) -> None:
        self.requests: list[str] = []

    async def chat(self, *, system, messages, **kwargs):
        self.requests.append(system + "\n" + "\n".join(message["content"] for message in messages))
        return "我想先听听。"


@pytest.mark.parametrize("agent_type", [FatherAgent, MotherAgent, PatientAgent])
def test_family_model_requests_exclude_internal_events(agent_type) -> None:
    session = Session(case_id="teen-truancy-01", school=School.STRUCTURAL, round=1)
    session.append_event(
        Event(round=1, actor=Actor.DOCTOR, type=EventType.UTTERANCE, content="PUBLIC_QUESTION")
    )
    session.append_event(
        Event(round=1, actor=Actor.OBSERVER, type=EventType.HINT, content="PRIVATE_SIDECHANNEL")
    )
    session.append_event(Event(round=1, actor=Actor.SYSTEM, type=EventType.SYSTEM, content="PRIVATE_SYSTEM"))
    llm = RecordingLLM()
    agent_type(llm=llm).respond(SessionContext(session=session))
    assert len(llm.requests) == 1
    assert "PUBLIC_QUESTION" in llm.requests[0]
    assert "PRIVATE_SIDECHANNEL" not in llm.requests[0]
    assert "PRIVATE_SYSTEM" not in llm.requests[0]


def test_observer_uses_selected_school_even_when_template_mentions_other_schools() -> None:
    session = Session(case_id="teen-truancy-01", school=School.SATIR, round=1)
    event = Event(round=1, actor=Actor.FATHER, type=EventType.UTTERANCE, content="你总是这样。")
    session.append_event(event)
    hint = ObserverAgent().observe(
        SessionContext(
            session=session, round_events=[event], school_template="Compare structural, Bowen and Satir."
        )
    )
    assert hint is not None
    assert hint.trigger == "blaming_pattern"


def test_observer_does_not_treat_doctors_boundary_reminder_as_parent_behavior() -> None:
    session = Session(case_id="teen-truancy-01", school=School.STRUCTURAL, round=1)
    event = Event(round=1, actor=Actor.DOCTOR, type=EventType.UTTERANCE, content="我们不要代替孩子说话。")
    session.append_event(event)
    assert ObserverAgent().observe(SessionContext(session=session, round_events=[event])) is None


def test_bad_case_fails_before_any_dialogue_is_generated() -> None:
    session = Session(case_id="missing-case", school=School.STRUCTURAL)
    with pytest.raises(FileNotFoundError):
        Orchestrator(max_rounds=1).run(session)
    assert session.events == []
