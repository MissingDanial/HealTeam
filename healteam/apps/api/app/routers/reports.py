"""Reports router — W1 stub.

W1: declares the shape of the report-fetch endpoint. W2 (post-supervision
meeting) populates it with real report data.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter()


class ReportSummary(BaseModel):
    session_id: str
    generated_at: str
    school: str
    summary_md: str = ""


@router.get(
    "/{session_id}",
    response_model=ReportSummary,
    summary="Get the supervision report for a session (stub)",
)
async def get_report(session_id: str) -> ReportSummary:
    """W1 stub: returns an empty summary. W2 returns the real report."""
    raise HTTPException(status_code=501, detail="Reports endpoint is W2 work")
