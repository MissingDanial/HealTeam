"""AI 旁观者 (Observer) agent.

The observer is the L1 evolution engine's input sensor:
- It never speaks in the room — it only whispers via hints.
- It detects patterns (stagnation, defense escalation, emotional breakthrough)
  and sends side-channel hints to the doctor.
- Uses school-specific trigger templates.
"""

from __future__ import annotations

import random
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


# School-specific trigger templates — expanded with more triggers.
_TRIGGERS = {
    "structural": [
        {
            "id": "boundary_violation",
            "keywords": ["代替", "替他", "帮她", "我来", "他不是", "他其实"],
            "hint": "检测到家长代替孩子发言的边界侵犯。建议使用结构式技术：明确划定发言边界，让每个家庭成员为自己发声。",
        },
        {
            "id": "coalition_detected",
            "keywords": ["我们俩", "我和他", "一起", "我们 vs"],
            "hint": "检测到可能的联盟形成。建议使用结构式技术：探索联盟的动力，重新划定家庭子系统边界。",
        },
        {
            "id": "enmeshment",
            "keywords": ["我替他", "帮他说", "我来解释", "他不懂"],
            "hint": "检测到过度卷入/纠缠模式。建议使用结构式技术：区分个体边界，促进自主表达。",
        },
        {
            "id": "disengagement",
            "keywords": ["无所谓", "随便", "不关我", "你看着办"],
            "hint": "检测到脱离/疏离模式。建议使用结构式技术：温和邀请该成员参与，探索疏离背后的情感。",
        },
    ],
    "bowen": [
        {
            "id": "triangulation",
            "keywords": ["都是他", "因为她", "怪", "都赖"],
            "hint": "检测到三角化模式——两人冲突时拉入第三方。建议使用 Bowen 技术：指出三角化模式，鼓励直接对话。",
        },
        {
            "id": "emotional_cutoff",
            "keywords": ["不想说", "无所谓", "随便", "不想提"],
            "hint": "检测到情感切断迹象。建议使用 Bowen 技术：温和探索切断背后的情感，促进 differentiation。",
        },
        {
            "id": "family_projection",
            "keywords": ["都是因为他", "都是她的错", "问题出在"],
            "hint": "检测到家庭投射过程。建议使用 Bowen 技术：探索焦虑如何在家庭中传递和聚焦。",
        },
    ],
    "satir": [
        {
            "id": "blaming_pattern",
            "keywords": ["都是你的错", "你总是", "你从来", "你就知道"],
            "hint": "检测到指责型沟通姿态。建议使用 Satir 技术：帮助家庭成员觉察自己的沟通姿态，引导一致性表达。",
        },
        {
            "id": "placating_pattern",
            "keywords": ["对不起", "是我的错", "都是我不好", "我不好"],
            "hint": "检测到讨好型沟通姿态。建议使用 Satir 技术：帮助该成员觉察自己的讨好模式，鼓励真实表达需求。",
        },
        {
            "id": "computing_pattern",
            "keywords": ["应该", "不应该", "合理", "不合理"],
            "hint": "检测到超理智型沟通姿态。建议使用 Satir 技术：引导该成员觉察情感，而非仅停留在逻辑层面。",
        },
    ],
}

# General triggers (cross-school) — expanded.
_GENERAL_TRIGGERS = [
    {
        "id": "stagnation_3_rounds",
        "check": "stagnation",
        "hint": "对话已停滞 3 轮。建议改变提问方式或引入新的治疗焦点，打破僵局。",
    },
    {
        "id": "defense_escalation",
        "check": "defense",
        "hint": "检测到防御机制升级。建议降低压力，使用共情反映，避免直接挑战防御。",
    },
    {
        "id": "emotional_breakthrough",
        "check": "breakthrough",
        "hint": "检测到情绪突破迹象。建议保持支持性态度，不要急于推进，让情感自然流动。",
    },
    {
        "id": "patient_silence",
        "check": "silence",
        "hint": "病人已沉默多轮。建议不要直接施压，可以转向其他家庭成员，或使用间接技术。",
    },
    {
        "id": "somatization",
        "check": "somatization",
        "hint": "检测到躯体化信号。建议将症状与家庭互动压力联结，探索症状的系统性意义。",
    },
    {
        "id": "overprotection",
        "check": "overprotection",
        "hint": "检测到过度保护模式。建议温和挑战代答行为，促进孩子的自主表达。",
    },
]


@dataclass
class ObserverAgent:
    name: str = "observer"
    role: str = "AI 旁观者"
    system_prompt_path: str = "observer.md"
    llm: Optional[LLMClient] = None

    def should_speak(self, context: SessionContext) -> bool:
        """The observer never speaks in the room — it only whispers via hints."""
        return False

    def respond(self, context: SessionContext) -> str:
        return make_placeholder_utterance(Actor.OBSERVER, context)

    def observe(self, context: SessionContext) -> Optional[Hint]:
        """Detect triggers and emit hints for the doctor.

        Uses keyword-based detection (fast, deterministic) and optional
        LLM-based pattern detection (slower, more nuanced).
        Deduplicates: at most 1 hint per trigger per round.
        """
        if not context.round_events:
            return None

        # Combine all recent event text.
        recent_text = " ".join(
            e.content for e in context.round_events if e.content
        )
        if not recent_text:
            return None

        # Get hints already sent this round (dedup).
        existing_triggers = set()
        for e in context.session.events:
            if e.round == context.session.round and e.actor == Actor.OBSERVER:
                existing_triggers.update(e.tags)

        # Get school-specific triggers.
        school = "structural"  # default
        if context.school_template:
            for s in ["structural", "bowen", "satir"]:
                if s in context.school_template.lower():
                    school = s
                    break

        # Check school-specific keyword triggers (once per trigger per round).
        school_triggers = _TRIGGERS.get(school, [])
        for trigger in school_triggers:
            if trigger["id"] in existing_triggers:
                continue
            keywords = trigger.get("keywords", [])
            if any(kw in recent_text for kw in keywords):
                return Hint(
                    round=context.session.round,
                    trigger=trigger["id"],
                    content=trigger["hint"],
                    confidence=0.7,
                )

        # Check general keyword-based triggers.
        # Somatization detection.
        if "somatization" not in existing_triggers:
            somatic_kw = ["肚子", "头疼", "不舒服", "难受", "恶心", "疼"]
            if any(kw in recent_text for kw in somatic_kw):
                return Hint(
                    round=context.session.round,
                    trigger="somatization",
                    content="检测到躯体化信号。建议将症状与家庭互动压力联结，探索症状的系统性意义。",
                    confidence=0.6,
                )

        # Overprotection detection.
        if "overprotection" not in existing_triggers:
            protect_kw = ["他不是", "他其实", "替他", "帮他", "我来"]
            if any(kw in recent_text for kw in protect_kw):
                return Hint(
                    round=context.session.round,
                    trigger="overprotection",
                    content="检测到过度保护模式。建议温和挑战代答行为，促进孩子的自主表达。",
                    confidence=0.6,
                )

        # Stagnation check.
        stagnation_count = self._count_stagnation(context)
        if stagnation_count >= 3 and "stagnation_3_rounds" not in existing_triggers:
            return Hint(
                round=context.session.round,
                trigger="stagnation_3_rounds",
                content="对话已停滞多轮。建议改变提问方式或引入新的治疗焦点。",
                confidence=0.8,
            )

        # Defense escalation — lowered threshold.
        defense_score = self._check_defense_escalation(context)
        if defense_score > 0.4 and "defense_escalation" not in existing_triggers:
            return Hint(
                round=context.session.round,
                trigger="defense_escalation",
                content="检测到防御机制升级。建议降低压力，使用共情反映。",
                confidence=defense_score,
            )

        # Patient silence check.
        if "patient_silence" not in existing_triggers:
            patient_events = [
                e for e in context.session.events[-10:]
                if e.actor == Actor.PATIENT
            ]
            if not patient_events and context.session.round > 2:
                return Hint(
                    round=context.session.round,
                    trigger="patient_silence",
                    content="病人已沉默多轮。建议不要直接施压，可以转向其他家庭成员。",
                    confidence=0.6,
                )

        # Emotional breakthrough detection.
        if "emotional_breakthrough" not in existing_triggers:
            breakthrough_kw = ["其实我", "我怕", "我担心", "我不想", "我恨", "我讨厌", "为什么"]
            if any(kw in recent_text for kw in breakthrough_kw):
                return Hint(
                    round=context.session.round,
                    trigger="emotional_breakthrough",
                    content="检测到情绪突破迹象。建议保持支持性态度，不要急于推进，让情感自然流动。",
                    confidence=0.6,
                )

        # LLM-based detection — now runs every 2 rounds instead of 3.
        if self.llm and context.session.round > 1 and context.session.round % 2 == 0:
            return self._llm_detect(context, recent_text)

        return None

    def _count_stagnation(self, context: SessionContext) -> int:
        """Count consecutive rounds with similar content (stagnation)."""
        events = context.session.events
        if len(events) < 6:
            return 0

        # Check last 3 rounds for content similarity.
        last_rounds_text = []
        for r in range(max(0, context.session.round - 3), context.session.round):
            round_text = " ".join(
                e.content for e in events if e.round == r and e.content
            )
            if round_text:
                last_rounds_text.append(round_text)

        if len(last_rounds_text) < 2:
            return 0

        # Simple similarity: check if the same keywords appear repeatedly.
        all_words = []
        for text in last_rounds_text:
            words = set(text.split())
            all_words.append(words)

        if len(all_words) >= 2:
            overlap = len(all_words[0] & all_words[1])
            total = len(all_words[0] | all_words[1])
            if total > 0 and overlap / total > 0.5:
                return len(last_rounds_text)

        return 0

    def _check_defense_escalation(self, context: SessionContext) -> float:
        """Check if defense mechanisms are escalating. Returns 0.0-1.0."""
        defense_keywords = [
            "不是", "没有", "不对", "才不是", "你才", "凭什么",
            "不想说", "无所谓", "随便", "不知道", "忘了", "不关",
            "不想", "别问", "别说了",
        ]
        recent_events = context.round_events or []
        if not recent_events:
            return 0.0

        defense_count = sum(
            1 for e in recent_events
            if e.content and any(kw in e.content for kw in defense_keywords)
        )
        return min(1.0, defense_count / max(1, len(recent_events)))

    def _llm_detect(self, context: SessionContext, recent_text: str) -> Optional[Hint]:
        """Use LLM for nuanced pattern detection."""
        import asyncio

        system = (
            "你是一位家庭治疗观察者。分析以下治疗对话片段，判断是否存在以下模式：\n"
            "1. 对话停滞（stagnation）\n"
            "2. 防御升级（defense_escalation）\n"
            "3. 情绪突破（emotional_breakthrough）\n"
            "4. 三角化（triangulation）\n"
            "5. 沟通姿态问题（blaming/placating/computing/distracting）\n"
            "6. 躯体化（somatization）\n"
            "7. 过度卷入（enmeshment）\n"
            "8. 脱离（disengagement）\n\n"
            "如果检测到模式，返回 JSON: {\"pattern\": \"...\", \"hint\": \"...\", \"confidence\": 0.0-1.0}\n"
            "如果没有明显模式，返回: {\"pattern\": null}"
        )

        async def _call():
            return await self.llm.chat(  # type: ignore[union-attr]
                system=system,
                messages=[{"role": "user", "content": f"最近对话:\n{recent_text}"}],
                max_tokens=256,
            )

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(asyncio.run, _call()).result()
            else:
                result = asyncio.run(_call())

            # Parse result.
            import json

            if isinstance(result, str):
                data = json.loads(result)
            else:
                data = result

            if data.get("pattern"):
                return Hint(
                    round=context.session.round,
                    trigger=data["pattern"],
                    content=data.get("hint", ""),
                    confidence=float(data.get("confidence", 0.5)),
                )
        except Exception:
            pass  # LLM detection is best-effort

        return None


__all__ = ["ObserverAgent"]
