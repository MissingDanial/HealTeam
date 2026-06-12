"""LLM-based milestone detector.

Checks whether therapy milestones have been achieved based on
the conversation content. Called by the orchestrator's update_memory node.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from healteam_memory import Actor, Event

from .base import LLMClient

logger = logging.getLogger("healteam.agents.milestone_detector")


async def detect_milestones(
    llm: LLMClient,
    *,
    round_events: list[Event],
    milestones_remaining: list[dict[str, Any]],
    round_number: int,
) -> list[str]:
    """Check if any milestones were achieved in this round.

    Returns a list of milestone IDs that were newly achieved.
    """
    if not milestones_remaining or not round_events:
        return []

    # Build conversation text.
    conversation = "\n".join(
        f"[{e.actor.value}]: {e.content}"
        for e in round_events
        if e.content
    )
    if not conversation:
        return []

    # Build milestone descriptions.
    milestone_desc = []
    for m in milestones_remaining:
        mid = m.get("id", "")
        desc = m.get("description", "")
        signals = m.get("validation_signals", [])
        signal_text = ", ".join(signals) if signals else "无"
        milestone_desc.append(f"- {mid}: {desc}\n  验证信号: {signal_text}")

    milestones_text = "\n".join(milestone_desc)

    system = (
        "你是一位家庭治疗里程碑检测专家。根据以下对话片段和期望里程碑，"
        "判断本轮对话中是否出现了里程碑达成的信号。\n\n"
        "请返回 JSON 格式:\n"
        '{"achieved": ["milestone_id_1", "milestone_id_2"]}\n\n'
        "规则:\n"
        "- 只返回确实有证据支持的里程碑 ID\n"
        "- 如果没有明确信号，返回空列表\n"
        "- 里程碑 ID 必须来自给定列表"
    )

    user_msg = f"本轮对话 (第 {round_number} 轮):\n{conversation}\n\n期望里程碑:\n{milestones_text}"

    try:
        result = await llm.chat(
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=256,
        )

        data = json.loads(result) if isinstance(result, str) else result
        achieved = data.get("achieved", [])

        # Validate IDs.
        valid_ids = {m.get("id") for m in milestones_remaining}
        return [mid for mid in achieved if mid in valid_ids]

    except Exception:
        logger.exception("Milestone detection failed")
        return []


def detect_milestones_keyword(
    round_events: list[Event],
    milestones_remaining: list[dict[str, Any]],
) -> list[str]:
    """Simple keyword-based milestone detection (no LLM required).

    Returns a list of milestone IDs that were newly achieved.
    """
    if not milestones_remaining or not round_events:
        return []

    recent_text = " ".join(e.content.lower() for e in round_events if e.content)
    if not recent_text:
        return []

    achieved: list[str] = []
    for m in milestones_remaining:
        signals = m.get("validation_signals", [])
        for sig in signals:
            if sig.lower() in recent_text:
                achieved.append(m.get("id", ""))
                break

    return achieved


__all__ = ["detect_milestones", "detect_milestones_keyword"]
