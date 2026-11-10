"""Local annotation revisions and descriptive agreement, separate from reports."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from healteam_memory.evaluation_contracts import (
    CalibrationSummary,
    ReviewHistory,
    ReviewRecord,
    ReviewSubmission,
)
from healteam_memory.review_store import review_context
from healteam_memory.training_store import StoreError

from ..services.calibration import calibration_export, calibration_summary
from .training import get_manager

router = APIRouter()
calibration_router = APIRouter()


async def _store(operation, *args):
    try:
        return await operation(*args)
    except StoreError as exc:
        detail = {"code": exc.code}
        if hasattr(exc, "current_revision"):
            detail["current_revision"] = exc.current_revision
        raise HTTPException(status_code=exc.status_code, detail=detail) from None
    except Exception:
        raise HTTPException(status_code=503, detail={"code": "storage_unavailable"}) from None


@router.get("/{session_id}/reviews", response_model=ReviewHistory)
async def read_reviews(session_id: UUID, request: Request):
    repository = get_manager(request).repository
    record = await _store(repository.get, session_id)
    try:
        binding = review_context(record)
    except StoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code}) from None
    items = await _store(repository.list_reviews, session_id)
    return {
        "session_id": session_id,
        **binding,
        "latest_revision": items[-1]["revision"] if items else 0,
        "items": items,
    }


@router.post("/{session_id}/reviews", response_model=ReviewRecord, status_code=201)
async def submit_review(session_id: UUID, command: ReviewSubmission, request: Request, response: Response):
    result = await _store(get_manager(request).repository.submit_review, session_id, command)
    response.status_code = 200 if result.replayed else 201
    return result.review


@calibration_router.get("", response_model=CalibrationSummary)
async def read_calibration(request: Request):
    samples = await _store(get_manager(request).repository.list_calibration_samples)
    return calibration_summary(samples)


@calibration_router.get("/export")
async def export_calibration(request: Request):
    samples = await _store(get_manager(request).repository.list_calibration_samples)
    return JSONResponse(
        calibration_export(samples),
        headers={
            "Content-Disposition": 'attachment; filename="healteam-calibration.json"',
            "Cache-Control": "no-store",
        },
    )
