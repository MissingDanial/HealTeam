"""Session runner — wraps Orchestrator with SSE event emission.

Runs the therapy session in a background thread and emits events
via an asyncio.Queue for SSE streaming.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from healteam_agents import (
    DoctorAgent,
    FatherAgent,
    MotherAgent,
    ObserverAgent,
    Orchestrator,
    PatientAgent,
    PsychiatristAgent,
    SupervisionOrchestrator,
    build_case_context,
    generate_report,
)
from healteam_agents.base import BaseAgent, Hint, SessionContext
from healteam_memory import Actor, School, Session

from ..resources import load_validated_case, project_root, validate_supporting_resources
from ..settings import Settings, load_settings
from .llm import AnthropicLLMClient as AnthropicLLMClient
from .llm import HTTPModelClient, build_llm_client

logger = logging.getLogger("healteam.session_runner")


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------


def load_session_llm_settings() -> Settings:
    """Compatibility entry point backed by the shared explicit settings."""
    return load_settings()


def build_session_llm_client() -> HTTPModelClient | None:
    """Only explicit mock mode returns placeholder agents."""
    return build_llm_client(load_settings())


# ---------------------------------------------------------------------------
# Event emitter wrapper
# ---------------------------------------------------------------------------


@dataclass
class EventEmitter:
    """Wraps agents to emit SSE events when they speak or observe."""

    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop

    def wrap_agent(self, actor: Actor, agent: BaseAgent) -> BaseAgent:
        """Return a wrapper that emits events after agent responds."""
        emitter = self

        class WrappedAgent:
            def __init__(self):
                self.name = agent.name
                self.role = agent.role
                self.system_prompt_path = agent.system_prompt_path
                self.llm = agent.llm

            def should_speak(self, context: SessionContext) -> bool:
                return agent.should_speak(context)

            def respond(self, context: SessionContext) -> str:
                text = agent.respond(context)
                # Emit utterance event.
                event_data = {
                    "type": "utterance",
                    "round": context.session.round,
                    "actor": actor.value,
                    "content": text,
                }
                asyncio.run_coroutine_threadsafe(emitter.queue.put(event_data), emitter.loop)
                return text

            def observe(self, context: SessionContext) -> Hint | None:
                hint = agent.observe(context)
                if hint is not None:
                    event_data = {
                        "type": "hint",
                        "round": context.session.round,
                        "trigger": hint.trigger,
                        "content": hint.content,
                        "confidence": hint.confidence,
                    }
                    asyncio.run_coroutine_threadsafe(emitter.queue.put(event_data), emitter.loop)
                return hint

        return WrappedAgent()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Session runner
# ---------------------------------------------------------------------------


async def run_session(
    case_id: str,
    school_str: str,
    max_rounds: int,
    queue: asyncio.Queue,
) -> dict[str, Any]:
    """Run a therapy session and emit events via the queue.

    Returns a dict with session results (for the 'complete' event).
    """
    settings = load_settings()
    load_validated_case(case_id)
    validate_supporting_resources(school_str)
    llm = build_session_llm_client()

    # Map school string to enum.
    school = School(school_str)

    # Create event emitter and wrap agents.
    loop = asyncio.get_running_loop()
    emitter = EventEmitter(queue=queue, loop=loop)

    raw_agents = {
        Actor.DOCTOR: DoctorAgent(llm=llm),
        Actor.FATHER: FatherAgent(llm=llm),
        Actor.MOTHER: MotherAgent(llm=llm),
        Actor.PATIENT: PatientAgent(llm=llm),
        Actor.OBSERVER: ObserverAgent(llm=llm),
        Actor.PSYCHIATRIST: PsychiatristAgent(llm=llm),
    }

    agents = {actor: emitter.wrap_agent(actor, agent) for actor, agent in raw_agents.items()}

    # Create orchestrator and session.
    orchestrator = Orchestrator(
        max_rounds=max_rounds,
        case_id=case_id,
        school=school,
        agents=agents,
    )
    session = Session(case_id=case_id, school=school)

    # Emit start event.
    await queue.put(
        {
            "type": "start",
            "case_id": case_id,
            "school": school_str,
            "max_rounds": max_rounds,
            "mode": "demo",
            "model_mode": settings.model_mode,
        }
    )

    # Run orchestrator in thread (it's synchronous).
    def _run():
        return orchestrator.run(session)

    final = await loop.run_in_executor(None, _run)
    if llm is not None and getattr(llm, "failure", None):
        await queue.put(
            {
                "type": "error",
                "code": "llm_failed",
                "message": "Model generation failed; no report was produced",
            }
        )
        return {}

    # Emit round summary.
    actor_counts: dict[str, int] = {}
    for evt in final.events:
        actor_counts[evt.actor.value] = actor_counts.get(evt.actor.value, 0) + 1

    await queue.put(
        {
            "type": "session_end",
            "rounds": final.round,
            "total_events": len(final.events),
            "status": final.status.value,
            "actor_counts": actor_counts,
        }
    )

    # Load case context once for supervision/report generation.
    case_context = build_case_context(case_id, school)

    # Run supervision meeting when a live LLM is configured.
    try:
        supervision_result: dict[str, str] | None = None
        if llm is not None:
            supervision = SupervisionOrchestrator(
                doctor=raw_agents[Actor.DOCTOR],
                observer=raw_agents[Actor.OBSERVER],
                psychiatrist=raw_agents[Actor.PSYCHIATRIST],
            )
            supervision_result = await supervision.run(final, case_context)
            if getattr(llm, "failure", None):
                await queue.put(
                    {
                        "type": "error",
                        "code": "llm_failed",
                        "message": "Model supervision failed; no report was produced",
                    }
                )
                return {}

            await queue.put(
                {
                    "type": "supervision",
                    "doctor_reflection": supervision_result.get("doctor_reflection", ""),
                    "observer_report": supervision_result.get("observer_report", ""),
                    "psychiatrist_analysis": supervision_result.get("psychiatrist_analysis", ""),
                    "consensus": supervision_result.get("consensus", ""),
                }
            )
        else:
            logger.info("Skipping model supervision in explicit mock demo mode")

        # Generate report.
        report = generate_report(
            session=final,
            case_context=case_context,
            supervision_result=supervision_result,
        )
        if settings.model_mode == "mock":
            report = "# 离线流程演示报告 / 固定占位回复，仅验证流程，不代表训练评价\n\n" + report

        # Save report.
        report_path = project_root() / "data" / "reports" / f"{final.id}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")

        await queue.put(
            {
                "type": "report",
                "content": report,
                "session_id": final.id,
                "model_mode": settings.model_mode,
            }
        )

    except Exception:
        logger.error("Supervision/report failed")
        await queue.put({"type": "error", "code": "report_failed", "message": "Report generation failed"})
        return {}

    # Signal completion.
    await queue.put({"type": "complete"})

    return {"session_id": final.id, "rounds": final.round}
