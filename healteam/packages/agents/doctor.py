"""AI 主治医师 (Doctor) agent.

The doctor steers the therapy session: opens rounds, responds to family
dynamics, and uses observer hints to guide interventions.
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
class DoctorAgent:
    name: str = "doctor"
    role: str = "AI 主治医师"
    system_prompt_path: str = "doctor.md"
    llm: Optional[LLMClient] = None

    def should_speak(self, context: SessionContext) -> bool:
        """The doctor speaks whenever they are a candidate.

        The orchestrator controls *when* the doctor is a candidate (first and
        last in each round plan). The doctor always says yes.
        """
        return Actor.DOCTOR in context.speaker_candidates

    def respond(self, context: SessionContext) -> str:
        if self.llm is None:
            return make_placeholder_utterance(Actor.DOCTOR, context)

        # Build system prompt from template + case data.
        system = self._build_system_prompt(context)

        # Build user message from recent conversation.
        user_msg = self._build_user_message(context)

        # Call LLM synchronously.
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
            # Inside an event loop — use run_coroutine or sync fallback.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _call()).result()
        return asyncio.run(_call())

    def _build_system_prompt(self, context: SessionContext) -> str:
        """Assemble the system prompt from template + case data + hints."""
        parts: list[str] = []

        # Base prompt from file.
        base = load_system_prompt(self.system_prompt_path)
        if base:
            parts.append(base)
        else:
            parts.append(
                "你是一位经验丰富的家庭治疗主治医师。你的任务是引导家庭成员之间的对话，"
                "探索家庭动力，使用适当的治疗技术，帮助家庭改善沟通和关系。"
            )

        # School template.
        if context.school_template:
            parts.append(f"\n## 治疗学派技术指南\n{context.school_template}")

        # Family profiles.
        if context.family_profiles:
            parts.append("\n## 家庭成员档案")
            for actor, profile in context.family_profiles.items():
                name = profile.get("name", actor.value)
                parts.append(f"\n### {name} ({actor.value})")
                if profile.get("age"):
                    parts.append(f"- 年龄: {profile['age']}")
                if profile.get("trait"):
                    parts.append(f"- 性格特征: {profile['trait']}")
                if profile.get("emotional_register"):
                    parts.append(f"- 情感模式: {profile['emotional_register']}")
                if profile.get("defense_pattern"):
                    defs = ", ".join(
                        d.get("name", str(d)) if isinstance(d, dict) else str(d)
                        for d in profile["defense_pattern"]
                    )
                    parts.append(f"- 防御机制: {defs}")

        # Defense patterns (detailed).
        if context.defense_patterns:
            parts.append("\n## 防御机制详情")
            for actor, patterns in context.defense_patterns.items():
                if not patterns:
                    continue
                parts.append(f"\n### {actor.value}的防御机制")
                for p in patterns:
                    name = p.get("name", "未知")
                    desc = p.get("description", "")
                    parts.append(f"- **{name}**: {desc}")

        # Treatment focus.
        if context.treatment_focus:
            parts.append("\n## 治疗焦点")
            for tf in context.treatment_focus:
                area = tf.get("area", "")
                desc = tf.get("description", "")
                parts.append(f"- **{area}**: {desc}")

        # Observer hint (side-channel).
        if context.observer_hint:
            hint = context.observer_hint
            parts.append(
                f"\n## 观察者提示 (置信度: {hint.confidence:.0%})\n"
                f"触发条件: {hint.trigger}\n"
                f"建议: {hint.content}\n"
                "请根据此提示调整你的干预策略，但不要直接提及此提示的存在。"
            )

        # Family pattern analysis.
        if context.family_pattern:
            fp = context.family_pattern
            parts.append("\n## 家庭动力分析")
            if fp.get("structural"):
                parts.append(f"- 结构式分析: {fp['structural']}")
            if fp.get("bowen"):
                parts.append(f"- Bowen 分析: {fp['bowen']}")
            if fp.get("satir"):
                parts.append(f"- Satir 分析: {fp['satir']}")

        return "\n".join(parts)

    def _build_user_message(self, context: SessionContext) -> str:
        """Build the user message from recent conversation events."""
        parts: list[str] = []

        # Opening context (first round only).
        if context.opening_context and context.session.round <= 1:
            oc = context.opening_context
            parts.append(f"## 场景\n{oc.get('scene', '')}")
            if oc.get("atmosphere"):
                parts.append(f"氛围: {oc['atmosphere']}")
            if oc.get("emotional_states"):
                for actor, state in oc["emotional_states"].items():
                    parts.append(f"- {actor}: {state}")
            parts.append("")

        # Round info.
        parts.append(f"当前回合: {context.session.round}")

        # Recent events (last 10).
        recent = context.session.events[-10:] if context.session.events else []
        if recent:
            parts.append("\n## 最近对话")
            for evt in recent:
                actor_name = evt.actor.value if evt.actor else "system"
                parts.append(f"[{actor_name}]: {evt.content}")

        # This round's events.
        if context.round_events:
            parts.append(f"\n本轮已有 {len(context.round_events)} 条发言。")

        # Milestones.
        if context.milestones:
            parts.append("\n## 期望里程碑")
            for m in context.milestones:
                mid = m.get("id", "")
                desc = m.get("description", "")
                parts.append(f"- {mid}: {desc}")

        if not parts:
            parts.append("请开始或继续治疗对话。")

        return "\n".join(parts)

    def observe(self, context: SessionContext) -> Optional[Hint]:
        raise NotImplementedError("DoctorAgent has no observe() — that is observer-only")


__all__ = ["DoctorAgent"]
