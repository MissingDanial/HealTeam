"""LLM-based emotion and relationship assessor.

Called by the orchestrator's update_memory node at the end of each round.
Uses the LLM to assess current emotional states and relationship dynamics
based on the round's conversation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from healteam_memory import (
    Actor,
    EmotionMap,
    Event,
    RelationType,
    RelationshipMap,
)

from .base import LLMClient

logger = logging.getLogger("healteam.agents.assessor")


# Emotion vocabulary (Chinese labels for natural use in prompts).
EMOTION_LABELS = [
    "愤怒", "悲伤", "恐惧", "焦虑", "羞耻", "内疚",
    "冷漠", "困惑", "希望", "感激", "平静", "紧张",
    "抵触", "防御", "脆弱", "委屈", "无奈", "绝望",
    "释然", "感动", "担忧", "受伤", "孤独", "压抑",
]


async def assess_emotions(
    llm: LLMClient,
    *,
    round_events: list[Event],
    previous_emotions: Optional[EmotionMap] = None,
    family_profiles: Optional[dict[Actor, dict[str, Any]]] = None,
) -> EmotionMap:
    """Assess the emotional state of each actor after a round.

    Returns a new EmotionMap with updated emotion labels.
    """
    if not round_events:
        return previous_emotions or EmotionMap()

    # Build conversation text.
    conversation = "\n".join(
        f"[{e.actor.value}]: {e.content}"
        for e in round_events
        if e.content
    )
    if not conversation:
        return previous_emotions or EmotionMap()

    # Build profile context.
    profile_text = ""
    if family_profiles:
        lines = []
        for actor, profile in family_profiles.items():
            name = profile.get("name", actor.value)
            trait = profile.get("trait", "")
            lines.append(f"- {name} ({actor.value}): {trait}")
        profile_text = "\n".join(lines)

    # Previous emotions context.
    prev_text = ""
    if previous_emotions and previous_emotions.emotions:
        prev_text = "\n".join(
            f"- {actor.value}: {label}"
            for actor, label in previous_emotions.emotions.items()
        )

    system = (
        "你是一位家庭治疗情绪评估专家。根据以下对话片段，评估每位家庭成员的当前情绪状态。\n\n"
        f"可选情绪标签: {', '.join(EMOTION_LABELS)}\n\n"
        "请返回 JSON 格式:\n"
        '{"emotions": {"father": "标签", "mother": "标签", "patient": "标签"}}\n\n'
        "规则:\n"
        "- 只评估家庭成员（father, mother, patient），不评估医生或观察者\n"
        "- 如果信息不足，使用前一轮的情绪标签\n"
        "- 情绪标签必须来自给定列表"
    )

    user_parts = [f"本轮对话:\n{conversation}"]
    if profile_text:
        user_parts.append(f"\n家庭成员特征:\n{profile_text}")
    if prev_text:
        user_parts.append(f"\n前一轮情绪:\n{prev_text}")

    user_msg = "\n".join(user_parts)

    try:
        result = await llm.chat(
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=256,
        )

        data = json.loads(result) if isinstance(result, str) else result
        emotions = data.get("emotions", {})

        emap = previous_emotions.model_copy(deep=True) if previous_emotions else EmotionMap()
        for actor_str, label in emotions.items():
            try:
                actor = Actor(actor_str)
                if actor in (Actor.FATHER, Actor.MOTHER, Actor.PATIENT):
                    emap.set(actor, str(label))
            except ValueError:
                pass

        return emap

    except Exception:
        logger.exception("Emotion assessment failed")
        return previous_emotions or EmotionMap()


async def assess_relationships(
    llm: LLMClient,
    *,
    round_events: list[Event],
    previous_relationships: Optional[RelationshipMap] = None,
    family_profiles: Optional[dict[Actor, dict[str, Any]]] = None,
) -> RelationshipMap:
    """Assess pairwise relationship dynamics after a round.

    Returns a new RelationshipMap with updated relation types.
    """
    if not round_events:
        return previous_relationships or RelationshipMap()

    conversation = "\n".join(
        f"[{e.actor.value}]: {e.content}"
        for e in round_events
        if e.content
    )
    if not conversation:
        return previous_relationships or RelationshipMap()

    system = (
        "你是一位家庭治疗关系评估专家。根据以下对话片段，评估家庭成员之间的关系动态。\n\n"
        f"关系类型: alliance(联盟), confrontation(对抗), neutral(中性), coalition(同盟)\n\n"
        "请返回 JSON 格式:\n"
        '{"relationships": {"father__mother": "类型", "father__patient": "类型", "mother__patient": "类型"}}\n\n'
        "规则:\n"
        "- 对 key 按字母排序（如 father__mother，不是 mother__father）\n"
        "- 只评估家庭成员之间的关系\n"
        "- 关系类型必须来自给定列表"
    )

    user_msg = f"本轮对话:\n{conversation}"

    try:
        result = await llm.chat(
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=256,
        )

        data = json.loads(result) if isinstance(result, str) else result
        rels = data.get("relationships", {})

        rmap = previous_relationships.model_copy(deep=True) if previous_relationships else RelationshipMap()
        for pair_key, rel_type in rels.items():
            try:
                rel = RelationType(rel_type)
                rmap.relationships[pair_key] = rel
            except ValueError:
                pass

        return rmap

    except Exception:
        logger.exception("Relationship assessment failed")
        return previous_relationships or RelationshipMap()


async def parse_interventions(
    llm: LLMClient,
    *,
    round_events: list[Event],
) -> list[dict[str, Any]]:
    """Detect therapeutic interventions used by the doctor in this round.

    Returns a list of intervention dicts with type, effectiveness, and notes.
    """
    doctor_events = [e for e in round_events if e.actor == Actor.DOCTOR and e.content]
    if not doctor_events:
        return []

    conversation = "\n".join(f"[doctor]: {e.content}" for e in doctor_events)

    system = (
        "你是一位家庭治疗技术识别专家。分析以下治疗师的发言，识别使用了哪些治疗技术。\n\n"
        "常见技术包括:\n"
        "- reframing (重构/重新框架)\n"
        "- circular_question (循环提问)\n"
        "- genogram (家谱图)\n"
        "- boundary_setting (边界设定)\n"
        "- joining (加入)\n"
        "- enactment (现场演绎)\n"
        "- tracking (追踪)\n"
        "- intensification (强化)\n"
        "- paradoxical_directive (矛盾处方)\n"
        "- empathic_reflection (共情反映)\n\n"
        "请返回 JSON 格式:\n"
        '{"interventions": [{"type": "技术名", "effectiveness": "positive/negative/neutral", "notes": "说明"}]}'
    )

    try:
        result = await llm.chat(
            system=system,
            messages=[{"role": "user", "content": conversation}],
            max_tokens=256,
        )

        data = json.loads(result) if isinstance(result, str) else result
        return data.get("interventions", [])

    except Exception:
        logger.exception("Intervention parsing failed")
        return []


__all__ = [
    "assess_emotions",
    "assess_relationships",
    "parse_interventions",
    "EMOTION_LABELS",
]
