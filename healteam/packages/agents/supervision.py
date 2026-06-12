"""Post-session supervision meeting graph.

A separate LangGraph that runs after the therapy session ends:
doctor_reflect -> observer_report -> psychiatrist_analyze -> consensus -> END

Each agent contributes their analysis, then the psychiatrist synthesizes
a consensus evaluation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from healteam_memory import (
    Actor,
    Session,
)

from .base import SessionContext
from .doctor import DoctorAgent
from .observer import ObserverAgent
from .psychiatrist import PsychiatristAgent

logger = logging.getLogger("healteam.agents.supervision")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class SupervisionState(TypedDict, total=False):
    """State for the post-session supervision meeting."""

    session: Session
    case_context: Optional[dict[str, Any]]

    # Agent reflections.
    doctor_reflection: str
    observer_report: str
    psychiatrist_analysis: str
    consensus: str


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------


def _make_node_doctor_reflect(doctor: DoctorAgent):
    async def doctor_reflect(state: SupervisionState) -> SupervisionState:
        session = state["session"]
        ctx = SessionContext(
            session=session,
            case_data=state.get("case_context", {}).get("case") if state.get("case_context") else None,
            family_profiles=state.get("case_context", {}).get("family_profiles") if state.get("case_context") else None,
            school_template=state.get("case_context", {}).get("school_template") if state.get("case_context") else None,
        )

        # Build reflection prompt.
        system = (
            "你是一位家庭治疗主治医师。治疗会话已经结束，现在进入督导会议。\n"
            "请反思你在本次治疗中的表现：\n"
            "1. 你使用了哪些治疗技术？效果如何？\n"
            "2. 你对家庭动力的理解是否准确？\n"
            "3. 哪些干预成功了？哪些需要改进？\n"
            "4. 如果再来一次，你会怎么做？\n\n"
            "请用中文回答，控制在 300-500 字。"
        )

        # Build conversation summary.
        events = session.events[-30:] if session.events else []
        conversation = "\n".join(
            f"[{e.actor.value}]: {e.content[:80]}..."
            if len(e.content) > 80 else f"[{e.actor.value}]: {e.content}"
            for e in events if e.content
        )

        user_msg = f"治疗会话共 {session.round} 轮\n\n对话摘要:\n{conversation}"

        try:
            result = await doctor.llm.chat(  # type: ignore[union-attr]
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=1024,
            )
            return {**state, "doctor_reflection": result}
        except Exception:
            logger.exception("Doctor reflection failed")
            return {**state, "doctor_reflection": "[医生反思生成失败]"}

    return doctor_reflect


def _make_node_observer_report(observer: ObserverAgent):
    async def observer_report(state: SupervisionState) -> SupervisionState:
        session = state["session"]
        ctx = SessionContext(
            session=session,
            case_data=state.get("case_context", {}).get("case") if state.get("case_context") else None,
        )

        system = (
            "你是一位家庭治疗观察者。治疗会话已经结束，现在进入督导会议。\n"
            "请报告你在本次治疗中观察到的情况：\n"
            "1. 家庭动力的变化轨迹\n"
            "2. 防御机制的演变\n"
            "3. 关键转折点\n"
            "4. 治疗师的干预时机和技术选择\n"
            "5. 未被治疗师注意到的重要信号\n\n"
            "请用中文回答，控制在 300-500 字。"
        )

        events = session.events[-30:] if session.events else []
        conversation = "\n".join(
            f"[{e.actor.value}]: {e.content[:80]}..."
            if len(e.content) > 80 else f"[{e.actor.value}]: {e.content}"
            for e in events if e.content
        )

        user_msg = f"治疗会话共 {session.round} 轮\n\n对话摘要:\n{conversation}"

        try:
            result = await observer.llm.chat(  # type: ignore[union-attr]
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=1024,
            )
            return {**state, "observer_report": result}
        except Exception:
            logger.exception("Observer report failed")
            return {**state, "observer_report": "[观察者报告生成失败]"}

    return observer_report


def _make_node_psychiatrist_analyze(psychiatrist: PsychiatristAgent):
    async def psychiatrist_analyze(state: SupervisionState) -> SupervisionState:
        session = state["session"]
        ctx = SessionContext(
            session=session,
            case_data=state.get("case_context", {}).get("case") if state.get("case_context") else None,
            family_profiles=state.get("case_context", {}).get("family_profiles") if state.get("case_context") else None,
            school_template=state.get("case_context", {}).get("school_template") if state.get("case_context") else None,
            treatment_focus=state.get("case_context", {}).get("treatment_focus") if state.get("case_context") else None,
            milestones=state.get("case_context", {}).get("milestones") if state.get("case_context") else None,
        )

        doctor_reflection = state.get("doctor_reflection", "")
        observer_report = state.get("observer_report", "")

        system = (
            "你是一位资深心理医生和督导。请根据医生的反思和观察者的报告，"
            "对本次治疗会话进行综合分析：\n"
            "1. 治疗师的专业水平评估（1-10 分）\n"
            "2. 主要亮点\n"
            "3. 需要改进的方面\n"
            "4. 具体改进建议\n"
            "5. 对家庭状况的整体评估\n\n"
            "请用中文回答，控制在 400-600 字。"
        )

        user_msg = (
            f"治疗会话共 {session.round} 轮\n\n"
            f"## 医生反思\n{doctor_reflection}\n\n"
            f"## 观察者报告\n{observer_report}"
        )

        try:
            result = await psychiatrist.llm.chat(  # type: ignore[union-attr]
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=1024,
            )
            return {**state, "psychiatrist_analysis": result}
        except Exception:
            logger.exception("Psychiatrist analysis failed")
            return {**state, "psychiatrist_analysis": "[心理医生分析生成失败]"}

    return psychiatrist_analyze


def _make_node_consensus(psychiatrist: PsychiatristAgent):
    async def consensus(state: SupervisionState) -> SupervisionState:
        system = (
            "你是一位资深督导。请综合以下信息，生成最终的督导共识：\n"
            "1. 本次治疗的总体评价\n"
            "2. 治疗师的成长建议\n"
            "3. 对家庭的后续建议\n\n"
            "请用中文回答，控制在 200-300 字。"
        )

        user_msg = (
            f"## 医生反思\n{state.get('doctor_reflection', '')}\n\n"
            f"## 观察者报告\n{state.get('observer_report', '')}\n\n"
            f"## 心理医生分析\n{state.get('psychiatrist_analysis', '')}"
        )

        try:
            result = await psychiatrist.llm.chat(  # type: ignore[union-attr]
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=512,
            )
            return {**state, "consensus": result}
        except Exception:
            logger.exception("Consensus generation failed")
            return {**state, "consensus": "[督导共识生成失败]"}

    return consensus


# ---------------------------------------------------------------------------
# Supervision Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class SupervisionOrchestrator:
    """Runs the post-session supervision meeting."""

    doctor: DoctorAgent = field(default_factory=DoctorAgent)
    observer: ObserverAgent = field(default_factory=ObserverAgent)
    psychiatrist: PsychiatristAgent = field(default_factory=PsychiatristAgent)
    _graph: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._graph = self._build_graph()

    def _build_graph(self):
        g = StateGraph(SupervisionState)

        g.add_node("doctor_reflect", _make_node_doctor_reflect(self.doctor))
        g.add_node("observer_report", _make_node_observer_report(self.observer))
        g.add_node("psychiatrist_analyze", _make_node_psychiatrist_analyze(self.psychiatrist))
        g.add_node("consensus", _make_node_consensus(self.psychiatrist))

        g.set_entry_point("doctor_reflect")
        g.add_edge("doctor_reflect", "observer_report")
        g.add_edge("observer_report", "psychiatrist_analyze")
        g.add_edge("psychiatrist_analyze", "consensus")
        g.add_edge("consensus", END)

        return g.compile()

    async def run(
        self,
        session: Session,
        case_context: Optional[dict[str, Any]] = None,
    ) -> SupervisionState:
        """Run the supervision meeting. Returns the final state."""
        initial: SupervisionState = {
            "session": session,
            "case_context": case_context,
            "doctor_reflection": "",
            "observer_report": "",
            "psychiatrist_analysis": "",
            "consensus": "",
        }
        return await self._graph.ainvoke(initial)


__all__ = ["SupervisionOrchestrator", "SupervisionState"]
