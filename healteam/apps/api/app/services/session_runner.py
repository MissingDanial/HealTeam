"""Session runner — wraps Orchestrator with SSE event emission.

Runs the therapy session in a background thread and emits events
via an asyncio.Queue for SSE streaming.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repo_bootstrap import bootstrap_local_packages

bootstrap_local_packages(REPO_ROOT, strict=False)

import anthropic
from healteam_memory import Actor, School, Session
from healteam_agents import (
    DoctorAgent,
    FatherAgent,
    MotherAgent,
    ObserverAgent,
    Orchestrator,
    PatientAgent,
    PsychiatristAgent,
    build_case_context,
    generate_report,
    SupervisionOrchestrator,
)
from healteam_agents.base import BaseAgent, Hint, SessionContext

logger = logging.getLogger("healteam.session_runner")


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

class AnthropicLLMClient:
    """Real LLM client using anthropic SDK."""

    def __init__(
        self,
        *,
        model: str = "mimo-v2.5-pro",
        base_url: str | None = None,
        api_key: str,
    ):
        self.client = anthropic.AsyncAnthropic(
            base_url=base_url,
            api_key=api_key,
        )
        self.model = model

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs,
    ) -> str:
        resp = await self.client.messages.create(
            model=model or self.model,
            system=system,
            messages=messages,
            temperature=temperature,
            max_tokens=max(max_tokens, 1024),
        )
        for block in resp.content:
            if hasattr(block, "text") and block.type == "text":
                return block.text
        return "..."


@dataclass(frozen=True)
class SessionLLMSettings:
    """Runtime config for the live session LLM."""

    auth_token: str | None
    base_url: str | None
    model: str


def load_session_llm_settings() -> SessionLLMSettings:
    """Load runtime LLM settings without embedding secrets in source."""

    return SessionLLMSettings(
        auth_token=os.getenv("ANTHROPIC_AUTH_TOKEN"),
        base_url=os.getenv("ANTHROPIC_BASE_URL"),
        model=os.getenv("ANTHROPIC_MODEL", os.getenv("LLM_MODEL", "mimo-v2.5-pro")),
    )


def build_session_llm_client() -> AnthropicLLMClient | None:
    """Build the live LLM client when credentials are present."""

    settings = load_session_llm_settings()
    if not settings.auth_token:
        logger.warning(
            "ANTHROPIC_AUTH_TOKEN is not configured; falling back to placeholder agent replies"
        )
        return None

    return AnthropicLLMClient(
        model=settings.model,
        base_url=settings.base_url,
        api_key=settings.auth_token,
    )


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
                asyncio.run_coroutine_threadsafe(
                    emitter.queue.put(event_data), emitter.loop
                )
                return text

            def observe(self, context: SessionContext) -> Optional[Hint]:
                hint = agent.observe(context)
                if hint is not None:
                    event_data = {
                        "type": "hint",
                        "round": context.session.round,
                        "trigger": hint.trigger,
                        "content": hint.content,
                        "confidence": hint.confidence,
                    }
                    asyncio.run_coroutine_threadsafe(
                        emitter.queue.put(event_data), emitter.loop
                    )
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
    llm = build_session_llm_client()

    # Map school string to enum.
    school_map = {"structural": School.STRUCTURAL, "bowen": School.BOWEN, "satir": School.SATIR}
    school = school_map.get(school_str, School.STRUCTURAL)

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
    await queue.put({"type": "start", "case_id": case_id, "school": school_str, "max_rounds": max_rounds})

    # Run orchestrator in thread (it's synchronous).
    def _run():
        return orchestrator.run(session)

    final = await loop.run_in_executor(None, _run)

    # Emit round summary.
    actor_counts: dict[str, int] = {}
    for evt in final.events:
        actor_counts[evt.actor.value] = actor_counts.get(evt.actor.value, 0) + 1

    await queue.put({
        "type": "session_end",
        "rounds": final.round,
        "total_events": len(final.events),
        "status": final.status.value,
        "actor_counts": actor_counts,
    })

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

            await queue.put({
                "type": "supervision",
                "doctor_reflection": supervision_result.get("doctor_reflection", ""),
                "observer_report": supervision_result.get("observer_report", ""),
                "psychiatrist_analysis": supervision_result.get("psychiatrist_analysis", ""),
                "consensus": supervision_result.get("consensus", ""),
            })
        else:
            logger.info("Skipping supervision because no live Anthropic credentials are configured")

        # Generate report.
        report = generate_report(
            session=final,
            case_context=case_context,
            supervision_result=supervision_result,
        )

        # Save report.
        report_path = REPO_ROOT / "data" / "reports" / f"{final.id[:8]}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")

        await queue.put({
            "type": "report",
            "content": report,
            "path": str(report_path),
        })

    except Exception as e:
        logger.exception("Supervision/report failed")
        await queue.put({"type": "error", "message": str(e)})

    # Signal completion.
    await queue.put({"type": "complete"})

    return {"session_id": final.id, "rounds": final.round}
