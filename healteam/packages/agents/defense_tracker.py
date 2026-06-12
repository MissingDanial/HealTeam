"""Defense mechanism tracker.

Tracks which defense mechanisms are active for each family member,
detects escalation patterns, and provides context for agents.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from healteam_memory import Actor, Event

from .base import LLMClient

logger = logging.getLogger("healteam.agents.defense_tracker")


@dataclass
class DefenseState:
    """Tracks active defense mechanisms per actor."""

    # Actor -> list of active defense mechanism names.
    active_defenses: dict[Actor, list[str]] = field(default_factory=dict)
    # Actor -> escalation level (0-3).
    escalation: dict[Actor, int] = field(default_factory=dict)
    # History of detected defenses: [{round, actor, defense, intensity}].
    history: list[dict[str, Any]] = field(default_factory=list)

    def get_active(self, actor: Actor) -> list[str]:
        return self.active_defenses.get(actor, [])

    def get_escalation(self, actor: Actor) -> int:
        return self.escalation.get(actor, 0)

    def record(self, actor: Actor, defense: str, round_number: int, intensity: float = 0.5) -> None:
        """Record a defense mechanism activation."""
        if actor not in self.active_defenses:
            self.active_defenses[actor] = []
        if defense not in self.active_defenses[actor]:
            self.active_defenses[actor].append(defense)

        self.history.append({
            "round": round_number,
            "actor": actor.value,
            "defense": defense,
            "intensity": intensity,
        })

    def escalate(self, actor: Actor) -> int:
        """Increase escalation level for an actor. Returns new level."""
        current = self.escalation.get(actor, 0)
        new_level = min(3, current + 1)
        self.escalation[actor] = new_level
        return new_level

    def de_escalate(self, actor: Actor) -> int:
        """Decrease escalation level for an actor. Returns new level."""
        current = self.escalation.get(actor, 0)
        new_level = max(0, current - 1)
        self.escalation[actor] = new_level
        return new_level


async def detect_defenses(
    llm: LLMClient,
    *,
    round_events: list[Event],
    defense_patterns: Optional[dict[Actor, list[dict[str, Any]]]] = None,
    current_state: Optional[DefenseState] = None,
    round_number: int = 0,
) -> DefenseState:
    """Detect defense mechanisms activated in this round.

    Returns an updated DefenseState.
    """
    state = current_state or DefenseState()

    if not round_events:
        return state

    # Build conversation text.
    conversation = "\n".join(
        f"[{e.actor.value}]: {e.content}"
        for e in round_events
        if e.content
    )
    if not conversation:
        return state

    # Build known defense patterns context.
    patterns_text = ""
    if defense_patterns:
        lines = []
        for actor, patterns in defense_patterns.items():
            if not patterns:
                continue
            defs = ", ".join(p.get("name", "") for p in patterns)
            lines.append(f"- {actor.value}: {defs}")
        if lines:
            patterns_text = "\n".join(lines)

    system = (
        "你是一位家庭治疗防御机制识别专家。分析以下对话片段，识别家庭成员使用的防御机制。\n\n"
        "常见防御机制:\n"
        "- rationalization (合理化)\n"
        "- intellectualization (理智化)\n"
        "- displacement (置换)\n"
        "- denial (否认)\n"
        "- projection (投射)\n"
        "- reaction_formation (反向形成)\n"
        "- regression (退行)\n"
        "- acting_out (付诸行动)\n"
        "- somatization (躯体化)\n"
        "- avoidance (回避)\n"
        "- minimization (淡化)\n"
        "- splitting (分裂)\n\n"
        "请返回 JSON 格式:\n"
        '{"defenses": [{"actor": "father", "defense": "rationalization", "intensity": 0.7}], '
        '"escalation": {"father": true, "mother": false, "patient": false}}'
    )

    user_parts = [f"本轮对话:\n{conversation}"]
    if patterns_text:
        user_parts.append(f"\n已知防御模式:\n{patterns_text}")

    user_msg = "\n".join(user_parts)

    try:
        result = await llm.chat(
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=256,
        )

        data = json.loads(result) if isinstance(result, str) else result

        # Process detected defenses.
        for item in data.get("defenses", []):
            actor_str = item.get("actor", "")
            defense = item.get("defense", "")
            intensity = float(item.get("intensity", 0.5))
            try:
                actor = Actor(actor_str)
                state.record(actor, defense, round_number, intensity)
            except ValueError:
                pass

        # Process escalation.
        escalation = data.get("escalation", {})
        for actor_str, is_escalating in escalation.items():
            try:
                actor = Actor(actor_str)
                if is_escalating:
                    state.escalate(actor)
                else:
                    state.de_escalate(actor)
            except ValueError:
                pass

    except Exception:
        logger.exception("Defense detection failed")

    return state


def detect_defenses_keyword(
    round_events: list[Event],
    defense_patterns: Optional[dict[Actor, list[dict[str, Any]]]] = None,
    current_state: Optional[DefenseState] = None,
    round_number: int = 0,
) -> DefenseState:
    """Simple keyword-based defense detection (no LLM required).

    Returns an updated DefenseState.
    """
    state = current_state or DefenseState()

    if not round_events:
        return state

    # Keyword mappings.
    defense_keywords = {
        "rationalization": ["因为", "所以", "其实", "本来", "实际上"],
        "denial": ["没有", "不是", "才没有", "才不是"],
        "displacement": ["都是", "怪", "都怨", "怨"],
        "projection": ["你才", "你自己", "你还说"],
        "avoidance": ["不想说", "无所谓", "随便", "不知道"],
        "minimization": ["没什么", "没什么大不了", "小事"],
        "acting_out": ["不干了", "我走", "滚"],
    }

    for event in round_events:
        if not event.content or event.actor not in (Actor.FATHER, Actor.MOTHER, Actor.PATIENT):
            continue

        content = event.content
        for defense, keywords in defense_keywords.items():
            if any(kw in content for kw in keywords):
                state.record(event.actor, defense, round_number, 0.5)

    return state


def build_defense_context(state: DefenseState, actor: Actor) -> str:
    """Build a text description of an actor's active defenses for prompt injection."""
    active = state.get_active(actor)
    escalation = state.get_escalation(actor)

    if not active and escalation == 0:
        return ""

    parts: list[str] = []
    if active:
        parts.append(f"当前激活的防御机制: {', '.join(active)}")
    if escalation >= 2:
        parts.append("防御升级警告: 该成员的防御正在加剧，建议降低压力。")
    elif escalation >= 1:
        parts.append("防御有所增强。")

    return "\n".join(parts)


__all__ = [
    "DefenseState",
    "detect_defenses",
    "detect_defenses_keyword",
    "build_defense_context",
]
