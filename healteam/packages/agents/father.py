"""AI 父亲 (Father) agent.

The father character uses defense mechanisms (rationalization, intellectualization,
displacement) and speaking patterns defined in the case data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from healteam_memory import Actor

from .base import (
    BaseAgent,
    Hint,
    LLMClient,
    SessionContext,
    load_system_prompt,
    make_placeholder_utterance,
    room_events,
)


@dataclass
class FatherAgent:
    name: str = "father"
    role: str = "AI 父亲"
    system_prompt_path: str = "father.md"
    llm: Optional[LLMClient] = None

    def should_speak(self, context: SessionContext) -> bool:
        """The father speaks selectively — sometimes withdrawing defensively.

        He speaks when:
        1. Directly addressed (name in last utterance) — high probability.
        2. Mother just criticized him — defensive response.
        3. Randomly in some rounds (controlled by orchestrator weights).
        4. Sometimes he refuses to speak (defensive withdrawal).
        """
        import random

        if Actor.FATHER not in context.speaker_candidates:
            return False

        round_events = context.round_events or []

        # Already spoke this round — unlikely to speak again.
        if any(e.actor == Actor.FATHER for e in round_events):
            if random.random() > 0.15:
                return False

        # Direct address — almost always responds.
        if context.last_event and context.last_event.type.value == "utterance":
            content_lc = context.last_event.content.lower()
            if any(kw in content_lc for kw in ["爸爸", "父亲", "丈夫", "建国"]):
                return random.random() < 0.9

        # Mother just criticized him — defensive reaction.
        mother_attacks = [e for e in round_events if e.actor == Actor.MOTHER and
                          any(kw in e.content for kw in ["他能", "心硬", "不管", "不问", "不关心", "说他"])]
        if mother_attacks:
            if random.random() < 0.7:
                return True

        # Father sometimes withdraws — especially in early rounds.
        rnd = context.session.round
        if rnd < 3:
            # Early rounds: father often stays silent (defensive withdrawal).
            return random.random() < 0.3
        elif rnd < 8:
            # Mid rounds: slightly more willing.
            return random.random() < 0.45
        else:
            # Later rounds: more engaged.
            return random.random() < 0.55

    def respond(self, context: SessionContext) -> str:
        if self.llm is None:
            return make_placeholder_utterance(Actor.FATHER, context)

        system = self._build_system_prompt(context)
        user_msg = self._build_user_message(context)

        import asyncio

        async def _call():
            return await self.llm.chat(  # type: ignore[union-attr]
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=2048,
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _call()).result()
        return asyncio.run(_call())

    def _build_system_prompt(self, context: SessionContext) -> str:
        parts: list[str] = []

        base = load_system_prompt(self.system_prompt_path)
        if base:
            parts.append(base)
        else:
            parts.append(
                "你扮演一位家庭治疗中的父亲角色。你是一个有防御机制的普通人，"
                "不是治疗师。用自然的口语化方式说话，展现你的性格特征和防御模式。"
            )

        # Family profile (self).
        if context.family_profiles and Actor.FATHER in context.family_profiles:
            profile = context.family_profiles[Actor.FATHER]
            parts.append("\n## 你的角色档案")
            if profile.get("name"):
                parts.append(f"- 姓名: {profile['name']}")
            if profile.get("age"):
                parts.append(f"- 年龄: {profile['age']}")
            if profile.get("occupation"):
                parts.append(f"- 职业: {profile['occupation']}")
            if profile.get("trait"):
                parts.append(f"- 性格: {profile['trait']}")
            if profile.get("emotional_register"):
                parts.append(f"- 情感模式: {profile['emotional_register']}")
            if profile.get("origin_family_note"):
                parts.append(f"- 原生家庭: {profile['origin_family_note']}")

        # Defense patterns.
        if context.defense_patterns and Actor.FATHER in context.defense_patterns:
            patterns = context.defense_patterns[Actor.FATHER]
            if patterns:
                parts.append("\n## 你的防御机制（在对话中自然展现）")
                for p in patterns:
                    name = p.get("name", "")
                    desc = p.get("description", "")
                    example = p.get("example", "")
                    parts.append(f"- **{name}**: {desc}")
                    if example:
                        parts.append(f"  示例: {example}")

        # Agent role hints.
        if context.agent_role_hints and "father" in context.agent_role_hints:
            hints = context.agent_role_hints["father"]
            parts.append("\n## 角色扮演指南")
            if hints.get("speaking_style"):
                parts.append(f"- 说话风格: {hints['speaking_style']}")
            if hints.get("first_utterance_draft"):
                parts.append(f"- 开场白参考: {hints['first_utterance_draft']}")

        parts.append(
            "\n## 重要规则\n"
            "- 你是家庭成员，不是治疗师\n"
            "- 用口语化的方式说话，不要用书面语\n"
            "- 自然地展现你的防御机制，不要刻意表演\n"
            "- 回复控制在 2-4 句话\n"
            "- 不要使用括号标注你的心理状态或动作"
        )

        return "\n".join(parts)

    def _build_user_message(self, context: SessionContext) -> str:
        parts: list[str] = []

        # Opening context (first round).
        if context.opening_context and context.session.round <= 1:
            oc = context.opening_context
            parts.append(f"场景: {oc.get('scene', '')}")
            if oc.get("emotional_states", {}).get("father"):
                parts.append(f"你当前的情绪: {oc['emotional_states']['father']}")
            parts.append("")

        # Recent conversation.
        recent = room_events(context.session)[-8:]
        if recent:
            parts.append("最近的对话:")
            for evt in recent:
                actor_name = evt.actor.value if evt.actor else "system"
                parts.append(f"[{actor_name}]: {evt.content}")
        else:
            parts.append("治疗刚开始，请根据你的角色设定开始发言。")

        return "\n".join(parts)

    def observe(self, context: SessionContext) -> Optional[Hint]:
        raise NotImplementedError("FatherAgent has no observe() — that is observer-only")


__all__ = ["FatherAgent"]
