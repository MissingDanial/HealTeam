"""Deterministic async templates for testing a practice workflow, not therapy."""

from __future__ import annotations

import asyncio
from typing import Any

from healteam_memory import Actor


class MockFamily:
    def actors(self, learner_content: str) -> tuple[Actor, ...]:
        selected = []
        for actor, words in (
            (Actor.FATHER, ("爸爸", "父亲", "爸")),
            (Actor.MOTHER, ("妈妈", "母亲", "妈")),
            (Actor.PATIENT, ("孩子", "子轩", "同学", "儿子")),
        ):
            if any(word in learner_content for word in words):
                selected.append(actor)
        return tuple(selected) or (Actor.FATHER, Actor.MOTHER, Actor.PATIENT)

    async def respond(
        self,
        actor: Actor,
        case: dict[str, Any],
        learner_content: str,
        round_number: int,
        *,
        delay: float,
    ) -> str:
        await asyncio.sleep(max(0, delay))
        name = case["family"][actor.value]["name"]
        excerpt = " ".join(learner_content.split())[:100]
        continuation = {
            Actor.FATHER: "我先说一件我看到的事，再留时间听其他人。",
            Actor.MOTHER: "我愿意先讲自己的想法，也让孩子自己说。",
            Actor.PATIENT: "我想先用自己的话说一点，再听听你们怎么理解。",
        }[actor]
        return f"（离线固定模板，第{round_number}轮）我是{name}。你刚才说“{excerpt}”。{continuation}"

    def doctor_demo(self, round_number: int) -> str:
        return f"（AI离线示范，第{round_number}轮）请爸爸、妈妈和孩子分别说一件刚才注意到的事。这里仅演示系统流程。"
