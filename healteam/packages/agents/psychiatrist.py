"""AI 心理医生 (Psychiatrist) agent.

The psychiatrist stays silent during therapy and only speaks in the
end-of-session "AI supervision meeting". Their respond() is used by the
supervision graph (Phase 6).
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
class PsychiatristAgent:
    name: str = "psychiatrist"
    role: str = "AI 心理医生"
    system_prompt_path: str = "psychiatrist.md"
    llm: Optional[LLMClient] = None

    def should_speak(self, context: SessionContext) -> bool:
        """The psychiatrist never speaks during live therapy."""
        return False

    def respond(self, context: SessionContext) -> str:
        """Generate a psychiatrist's analysis (used in supervision meeting)."""
        if self.llm is None:
            return make_placeholder_utterance(Actor.PSYCHIATRIST, context)

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
                "你是一位资深心理医生和督导。在督导会议中，你需要：\n"
                "1. 分析治疗师（医生）的干预策略和技术使用\n"
                "2. 评估家庭动力的变化和进展\n"
                "3. 指出治疗中的亮点和改进空间\n"
                "4. 提供具体的改进建议\n"
                "5. 评估治疗师的共情能力和专业成长"
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
                if profile.get("trait"):
                    parts.append(f"- 性格特征: {profile['trait']}")
                if profile.get("defense_pattern"):
                    defs = ", ".join(
                        d.get("name", str(d)) if isinstance(d, dict) else str(d)
                        for d in profile["defense_pattern"]
                    )
                    parts.append(f"- 防御机制: {defs}")

        # Treatment focus.
        if context.treatment_focus:
            parts.append("\n## 治疗焦点")
            for tf in context.treatment_focus:
                area = tf.get("area", "")
                desc = tf.get("description", "")
                parts.append(f"- **{area}**: {desc}")

        # Milestones.
        if context.milestones:
            parts.append("\n## 期望里程碑")
            for m in context.milestones:
                mid = m.get("id", "")
                desc = m.get("description", "")
                parts.append(f"- {mid}: {desc}")

        parts.append(
            "\n## 督导规则\n"
            "- 以专业、建设性的方式提供反馈\n"
            "- 先肯定亮点，再指出改进空间\n"
            "- 提供具体、可操作的建议\n"
            "- 关注治疗师的专业成长"
        )

        return "\n".join(parts)

    def _build_user_message(self, context: SessionContext) -> str:
        parts: list[str] = []

        # Session summary.
        parts.append(f"治疗会话共 {context.session.round} 轮")

        # Recent conversation (for supervision context).
        recent = context.session.events[-20:] if context.session.events else []
        if recent:
            parts.append("\n## 治疗对话摘要")
            for evt in recent:
                actor_name = evt.actor.value if evt.actor else "system"
                content = evt.content[:100] + "..." if len(evt.content) > 100 else evt.content
                parts.append(f"[{actor_name}]: {content}")

        # Observer hints.
        if context.observer_hint:
            parts.append(f"\n观察者提示: {context.observer_hint.content}")

        if not parts:
            parts.append("请对本次治疗会话进行督导分析。")

        return "\n".join(parts)

    def observe(self, context: SessionContext) -> Optional[Hint]:
        raise NotImplementedError("PsychiatristAgent has no observe() — that is observer-only")


__all__ = ["PsychiatristAgent"]
