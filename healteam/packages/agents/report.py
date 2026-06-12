"""Evaluation report generator.

Generates a structured markdown report after the therapy session
and supervision meeting are complete.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from healteam_memory import (
    Actor,
    Event,
    Session,
)

logger = logging.getLogger("healteam.agents.report")


def generate_report(
    session: Session,
    case_context: Optional[dict[str, Any]] = None,
    supervision_result: Optional[dict[str, str]] = None,
    milestones_achieved: Optional[list[str]] = None,
    milestones_remaining: Optional[list[dict[str, Any]]] = None,
    hint_history: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Generate a structured markdown evaluation report.

    Returns the report as a markdown string.
    """
    sections: list[str] = []

    # Header.
    sections.append("# 家庭治疗模拟评估报告\n")
    sections.append(f"**案例 ID**: {session.case_id}")
    sections.append(f"**治疗学派**: {session.school.value}")
    sections.append(f"**会话轮数**: {session.round}")
    sections.append(f"**会话状态**: {session.status.value}")
    sections.append("")

    # Case summary.
    if case_context:
        case = case_context.get("case", {})
        sections.append("## 案例概要\n")
        if case.get("title"):
            sections.append(f"**标题**: {case['title']}")
        if case.get("description"):
            sections.append(f"**描述**: {case['description']}")
        sections.append("")

        # Family members.
        profiles = case_context.get("family_profiles", {})
        if profiles:
            sections.append("### 家庭成员\n")
            sections.append("| 角色 | 姓名 | 年龄 | 性格特征 |")
            sections.append("|------|------|------|----------|")
            for actor, profile in profiles.items():
                name = profile.get("name", actor.value)
                age = profile.get("age", "-")
                trait = profile.get("trait", "-")
                sections.append(f"| {actor.value} | {name} | {age} | {trait} |")
            sections.append("")

    # Conversation log.
    sections.append("## 治疗对话记录\n")
    events = session.events if session.events else []
    current_round = -1
    for evt in events:
        if evt.round != current_round:
            current_round = evt.round
            sections.append(f"\n### 第 {current_round} 轮\n")
        actor_name = evt.actor.value if evt.actor else "system"
        if evt.type.value == "hint":
            sections.append(f"[{actor_name} - 旁观者提示]: {evt.content}")
        else:
            sections.append(f"[{actor_name}]: {evt.content}")
    sections.append("")

    # Emotion tracking.
    if session.emotions and session.emotions.emotions:
        sections.append("## 情绪追踪\n")
        sections.append("| 角色 | 最终情绪 |")
        sections.append("|------|----------|")
        for actor, label in session.emotions.emotions.items():
            sections.append(f"| {actor.value} | {label} |")
        sections.append("")

    # Relationship dynamics.
    if session.relationships and session.relationships.relationships:
        sections.append("## 关系动态\n")
        sections.append("| 关系对 | 关系类型 |")
        sections.append("|--------|----------|")
        for pair, rel_type in session.relationships.relationships.items():
            sections.append(f"| {pair} | {rel_type.value} |")
        sections.append("")

    # Interventions.
    if session.interventions:
        sections.append("## 干预技术分析\n")
        sections.append("| 轮次 | 技术 | 效果 | 备注 |")
        sections.append("|------|------|------|------|")
        for iv in session.interventions:
            sections.append(
                f"| {iv.round} | {iv.type} | {iv.effectiveness.value} | {iv.notes} |"
            )
        sections.append("")

    # Milestones.
    sections.append("## 里程碑达成情况\n")
    if milestones_achieved:
        sections.append(f"**已达成**: {', '.join(milestones_achieved)}")
    if milestones_remaining:
        remaining_ids = [m.get("id", "") for m in milestones_remaining]
        sections.append(f"**未达成**: {', '.join(remaining_ids)}")
    if not milestones_achieved and not milestones_remaining:
        sections.append("无里程碑数据")
    sections.append("")

    # Observer hints summary.
    if hint_history:
        sections.append("## 观察者提示记录\n")
        sections.append("| 轮次 | 触发条件 | 提示内容 | 置信度 |")
        sections.append("|------|----------|----------|--------|")
        for h in hint_history:
            sections.append(
                f"| {h.get('round', '-')} | {h.get('trigger', '-')} | {h.get('content', '-')} | {h.get('confidence', 0):.0%} |"
            )
        sections.append("")

    # Supervision results.
    if supervision_result:
        sections.append("## 督导会议\n")
        if supervision_result.get("doctor_reflection"):
            sections.append("### 医生反思\n")
            sections.append(supervision_result["doctor_reflection"])
            sections.append("")
        if supervision_result.get("observer_report"):
            sections.append("### 观察者报告\n")
            sections.append(supervision_result["observer_report"])
            sections.append("")
        if supervision_result.get("psychiatrist_analysis"):
            sections.append("### 心理医生分析\n")
            sections.append(supervision_result["psychiatrist_analysis"])
            sections.append("")
        if supervision_result.get("consensus"):
            sections.append("### 督导共识\n")
            sections.append(supervision_result["consensus"])
            sections.append("")

    # Defense mechanism summary.
    if case_context:
        defense_patterns = case_context.get("defense_patterns", {})
        if defense_patterns:
            sections.append("## 防御机制概览\n")
            for actor, patterns in defense_patterns.items():
                if not patterns:
                    continue
                sections.append(f"### {actor.value}")
                for p in patterns:
                    name = p.get("name", "")
                    desc = p.get("description", "")
                    sections.append(f"- **{name}**: {desc}")
                sections.append("")

    # Footer.
    sections.append("---\n")
    sections.append("*本报告由 HealTeam AI 家庭治疗模拟系统自动生成*")

    return "\n".join(sections)


__all__ = ["generate_report"]
