"""Durable local training resources. Subscribing never starts a runner."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from healteam_memory.contracts import CreateSessionCommand, SubmitMessageCommand

from ..response_models import MessageReceipt, SessionHistory, SessionView, WorkflowReport

router = APIRouter()
reports_router = APIRouter()
_TERMINAL = {"completed", "cancelled", "failed", "interrupted"}


def get_manager(request: Request) -> Any:
    manager = getattr(request.app.state, "training_manager", None)
    if manager is None:
        code = getattr(request.app.state, "training_error", None) or "training_unavailable"
        raise HTTPException(status_code=503, detail={"code": code})
    return manager


@router.post(
    "",
    status_code=201,
    response_model=SessionView,
    summary="Create a durable local session",
    responses={200: {"description": "Existing session returned for an identical creation request"}},
)
async def create_session(command: CreateSessionCommand, request: Request, response: Response):
    view, created = await get_manager(request).create(command)
    response.status_code = 201 if created else 200
    return view


@router.get("", response_model=SessionHistory, summary="List local session history")
async def list_sessions(
    request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20, offset: Annotated[int, Query(ge=0)] = 0
):
    return await get_manager(request).list_sessions(limit=limit, offset=offset)


@router.get("/{session_id}", response_model=SessionView, summary="Read a durable session and transcript")
async def read_session(session_id: UUID, request: Request):
    return await get_manager(request).snapshot(str(session_id))


@router.post(
    "/{session_id}/messages",
    status_code=202,
    response_model=MessageReceipt,
    summary="Accept one learner turn",
)
async def submit_message(session_id: UUID, command: SubmitMessageCommand, request: Request):
    return await get_manager(request).submit(str(session_id), command)


@router.post(
    "/{session_id}/finish",
    status_code=202,
    response_model=SessionView,
    summary="End practice and create a workflow report",
)
async def finish_session(session_id: UUID, request: Request):
    return await get_manager(request).finish(str(session_id))


@router.post(
    "/{session_id}/cancel", status_code=202, response_model=SessionView, summary="Cancel further generation"
)
async def cancel_session(session_id: UUID, request: Request):
    return await get_manager(request).cancel(str(session_id))


@router.get("/{session_id}/events", summary="Replay and subscribe to persisted events")
async def session_events(
    session_id: UUID,
    request: Request,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header()] = None,
):
    manager = get_manager(request)
    cursor = after
    if last_event_id is not None:
        try:
            header_cursor = int(last_event_id)
            if header_cursor < 0:
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "invalid_event_cursor"}) from None
        cursor = max(cursor, header_cursor)
    initial = await manager.snapshot(str(session_id))
    if cursor > initial["last_event_seq"]:
        raise HTTPException(status_code=409, detail={"code": "event_cursor_ahead"})

    async def stream():
        nonlocal cursor
        heartbeat = time.monotonic()
        while not await request.is_disconnected():
            # The snapshot and all following visible events are read from one
            # committed record, so there is no subscribe-after-read gap.
            view, events = await manager.events_after(str(session_id), after_seq=cursor)
            for event in events:
                cursor = event.seq
                yield f"id: {event.seq}\nevent: {event.type.value}\ndata: {event.model_dump_json()}\n\n"
            # Private events may leave sequence gaps. They are consumed, never
            # sent to the browser, from this same consistent record snapshot.
            cursor = max(cursor, view["last_event_seq"])
            if view["state"] in _TERMINAL:
                break
            if time.monotonic() - heartbeat >= 10:
                yield ": keep-alive\n\n"
                heartbeat = time.monotonic()
            await asyncio.sleep(0.15)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            # Next's compression middleware honors no-transform. Without it,
            # gzip can hold a small live SSE batch until its buffer fills.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@reports_router.get(
    "/{session_id}", response_model=WorkflowReport, summary="Read saved workflow report status and content"
)
async def read_report(session_id: UUID, request: Request):
    return await get_manager(request).report(str(session_id))


@reports_router.post(
    "/{session_id}/retry",
    status_code=202,
    response_model=SessionView,
    summary="Retry review without replaying practice",
)
async def retry_report(session_id: UUID, request: Request):
    return await get_manager(request).retry_report(str(session_id))
