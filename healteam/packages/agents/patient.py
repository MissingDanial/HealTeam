"""AI 病人 (Patient, e.g. 青少年) agent.

The patient carries the heaviest defensive machinery: avoidance, regression,
acting out, somatization, etc. Emotional arc: rounds 1-5 silent/resistant,
rounds 6-10 opening up, rounds 10-15 breakthrough.
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
)


@dataclass
class PatientAgent:
    name: str = "patient"
    role: str = "AI 病人"
    system_prompt_path: str = "patient.md"
    llm: Optional[LLMClient] = None

    def should_speak(self, context: SessionContext) -> bool:
        """The patient speaks based on emotional arc with natural variability.

        - Rounds 0-2: Mostly silent (only when directly addressed, and even then reluctant).
        - Rounds 3-5: Reluctant responses, sometimes still refuses.
        - Rounds 6-9: Opening up, more willing.
        - Rounds 10+: Breakthrough territory, sometimes unexpectedly expressive.
        """
        import random

        if Actor.PATIENT not in context.speaker_candidates:
            return False

        rnd = context.session.round
        round_events = context.round_events or []

        # Already spoke this round — very unlikely to speak again.
        if any(e.actor == Actor.PATIENT for e in round_events):
            return random.random() < 0.1

        # Direct address — but still probabilistic (patient sometimes ignores).
        if context.last_event and context.last_event.type.value == "utterance":
            content_lc = context.last_event.content.lower()
            if any(kw in content_lc for kw in ["子轩", "孩子", "同学", "儿子"]):
                if rnd < 3:
                    return random.random() < 0.5  # Often still refuses early on.
                elif rnd < 6:
                    return random.random() < 0.75
                else:
                    return random.random() < 0.9

        # Mother over-protecting — patient might react with frustration.
        mother_smothering = [e for e in round_events if e.actor == Actor.MOTHER and
                             any(kw in e.content for kw in ["他不是", "他其实", "替他", "帮他"])]
        if mother_smothering:
            if random.random() < 0.5:
                return True

        # Emotional arc with randomness.
        if rnd < 3:
            return random.random() < 0.1  # Very resistant.
        elif rnd < 6:
            return random.random() < 0.25  # Reluctant.
        elif rnd < 10:
            return random.random() < 0.4  # Opening up.
        else:
            return random.random() < 0.55  # Breakthrough territory.

    def respond(self, context: SessionContext) -> str:
        if self.llm is None:
            return make_placeholder_utterance(Actor.PATIENT, context)

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
                "你扮演一位家庭治疗中的青少年病人角色。你有各种防御机制，"
                "对治疗有抵触情绪。用青少年的口语方式说话，展现你的内心挣扎。"
            )

        # Family profile (self).
        if context.family_profiles and Actor.PATIENT in context.family_profiles:
            profile = context.family_profiles[Actor.PATIENT]
            parts.append("\n## 你的角色档案")
            if profile.get("name"):
                parts.append(f"- 姓名: {profile['name']}")
            if profile.get("age"):
                parts.append(f"- 年龄: {profile['age']}")
            if profile.get("trait"):
                parts.append(f"- 性格: {profile['trait']}")
            if profile.get("emotional_register"):
                parts.append(f"- 情感模式: {profile['emotional_register']}")
            if profile.get("origin_family_note"):
                parts.append(f"- 原生家庭: {profile['origin_family_note']}")

        # Defense patterns.
        if context.defense_patterns and Actor.PATIENT in context.defense_patterns:
            patterns = context.defense_patterns[Actor.PATIENT]
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
        if context.agent_role_hints and "patient" in context.agent_role_hints:
            hints = context.agent_role_hints["patient"]
            parts.append("\n## 角色扮演指南")
            if hints.get("speaking_style"):
                parts.append(f"- 说话风格: {hints['speaking_style']}")
            if hints.get("first_utterance_draft"):
                parts.append(f"- 开场白参考: {hints['first_utterance_draft']}")

        # Emotional arc guidance.
        rnd = context.session.round
        if rnd < 3:
            parts.append(
                "\n## 当前情绪阶段: 抵触期\n"
                "- 你对治疗非常抵触，不愿意说话\n"
                "- 回复尽量简短（1-2 句），可以答非所问\n"
                "- 展现回避、沉默、防御的姿态"
            )
        elif rnd < 6:
            parts.append(
                "\n## 当前情绪阶段: 试探期\n"
                "- 你开始试探性地回应，但仍然保持警惕\n"
                "- 回复可以稍长（2-3 句），但仍有防御\n"
                "- 偶尔流露出真实感受，但很快收回"
            )
        elif rnd < 10:
            parts.append(
                "\n## 当前情绪阶段: 开放期\n"
                "- 你开始愿意分享内心感受\n"
                "- 回复可以更长（3-4 句），展现情感波动\n"
                "- 可以出现情绪爆发或脆弱时刻"
            )
        else:
            parts.append(
                "\n## 当前情绪阶段: 突破期\n"
                "- 你可能出现情绪崩溃或重大领悟\n"
                "- 展现深层情感和原生家庭创伤\n"
                "- 可以有转折性的发言"
            )

        parts.append(
            "\n## 重要规则\n"
            "- 你是青少年病人，不是治疗师\n"
            "- 用青少年的口语方式说话（可以有网络用语、口头禅）\n"
            "- 自然地展现你的防御机制\n"
            "- 回复不要过长\n"
            "- 不要使用括号标注你的心理状态或动作"
        )

        return "\n".join(parts)

    def _build_user_message(self, context: SessionContext) -> str:
        parts: list[str] = []

        # Opening context (first round).
        if context.opening_context and context.session.round <= 1:
            oc = context.opening_context
            parts.append(f"场景: {oc.get('scene', '')}")
            if oc.get("emotional_states", {}).get("patient"):
                parts.append(f"你当前的情绪: {oc['emotional_states']['patient']}")
            parts.append("")

        # Recent conversation.
        recent = context.session.events[-8:] if context.session.events else []
        if recent:
            parts.append("最近的对话:")
            for evt in recent:
                actor_name = evt.actor.value if evt.actor else "system"
                parts.append(f"[{actor_name}]: {evt.content}")
        else:
            parts.append("治疗刚开始。你被迫来参加家庭治疗，心里很不情愿。")

        return "\n".join(parts)

    def observe(self, context: SessionContext) -> Optional[Hint]:
        raise NotImplementedError("PatientAgent has no observe() — that is observer-only")


__all__ = ["PatientAgent"]
