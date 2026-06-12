"""Agents router — W1 stub.

W1: lists the 6 agent roles. W2 adds the supervision-meeting endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel


router = APIRouter()


class AgentInfo(BaseModel):
    name: str
    role: str
    speaks_in_session: bool


AGENTS: list[AgentInfo] = [
    AgentInfo(name="doctor", role="AI 主治医师", speaks_in_session=True),
    AgentInfo(name="psychiatrist", role="AI 心理医生", speaks_in_session=False),
    AgentInfo(name="observer", role="AI 旁观者", speaks_in_session=False),
    AgentInfo(name="father", role="AI 父亲", speaks_in_session=True),
    AgentInfo(name="mother", role="AI 母亲", speaks_in_session=True),
    AgentInfo(name="patient", role="AI 病人", speaks_in_session=True),
]


@router.get("", response_model=list[AgentInfo], summary="List the 6 HealTeam agents")
async def list_agents() -> list[AgentInfo]:
    return AGENTS
