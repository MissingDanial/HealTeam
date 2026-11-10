"""Sessions API router — SSE streaming therapy sessions."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..resources import CASE_ID_PATTERN, CaseResourceError, load_validated_case, validate_supporting_resources
from ..services.session_runner import run_session
from ..settings import ConfigurationError, load_settings

router = APIRouter()
logger = logging.getLogger("healteam.api.sessions")

# In-memory session history.
_session_history: list[dict[str, object]] = []


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(default="teen-truancy-01", pattern=CASE_ID_PATTERN, max_length=128)
    school: Literal["structural", "bowen", "satir"] = "structural"
    max_rounds: int = Field(default=10, ge=1, le=30, strict=True)
    mode: Literal["demo"] = "demo"


@router.post("", summary="Create and run a therapy session (SSE stream)")
async def create_session(req: SessionCreate, request: Request):
    """Create a new therapy session and stream events via SSE.

    Returns a Server-Sent Events stream with event types:
    - start: session started
    - utterance: agent spoke
    - hint: observer hint
    - round_end: round completed
    - session_end: session finished
    - supervision: supervision meeting results
    - report: evaluation report
    - complete: all done
    - error: error occurred
    """
    if not getattr(request.app.state, "allow_legacy_demo", False):
        raise HTTPException(status_code=410, detail={"code": "legacy_demo_replaced"})
    try:
        load_validated_case(req.case_id)
        validate_supporting_resources(req.school)
    except CaseResourceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code}) from None
    try:
        load_settings()
    except ConfigurationError as exc:
        raise HTTPException(status_code=503, detail={"code": str(exc)}) from None
    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream() -> AsyncGenerator[str, None]:
        # Start the session runner in the background.
        task = asyncio.create_task(
            run_session(
                case_id=req.case_id,
                school_str=req.school,
                max_rounds=req.max_rounds,
                queue=queue,
            )
        )
        result: dict[str, object] = {}
        queue_task: asyncio.Task | None = None

        try:
            while True:
                queue_task = asyncio.create_task(queue.get())
                done, pending = await asyncio.wait(
                    {queue_task, task},
                    timeout=600,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if queue_task in pending:
                    queue_task.cancel()
                    await asyncio.gather(queue_task, return_exceptions=True)

                if not done:
                    yield f"event: error\ndata: {json.dumps({'message': 'Timeout'})}\n\n"
                    break

                if queue_task in done:
                    event = queue_task.result()
                else:
                    try:
                        outcome = task.result()
                    except Exception:
                        logger.error("Session runner crashed")
                        yield f"event: error\ndata: {json.dumps({'code': 'session_failed', 'message': 'Session failed'})}\n\n"
                        break

                    if isinstance(outcome, dict):
                        result = outcome

                    if queue.empty():
                        yield "event: complete\ndata: {}\n\n"
                        break

                    continue

                event_type = event.get("type", "unknown")
                data = json.dumps(event, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {data}\n\n"

                if event_type == "complete":
                    break
                elif event_type == "error":
                    break

        except asyncio.CancelledError:
            logger.info("SSE client disconnected before session completion")
            raise
        except Exception:
            logger.error("SSE stream error")
            yield f"event: error\ndata: {json.dumps({'code': 'stream_failed', 'message': 'Session stream failed'})}\n\n"
        finally:
            if queue_task is not None and not queue_task.done():
                queue_task.cancel()
                await asyncio.gather(queue_task, return_exceptions=True)
            if not task.done():
                task.cancel()
            outcome = (await asyncio.gather(task, return_exceptions=True))[0]
            if isinstance(outcome, dict):
                result = outcome
            elif isinstance(outcome, Exception) and not isinstance(outcome, asyncio.CancelledError):
                logger.error("Session runner failed (%s)", type(outcome).__name__)

            if result:
                _session_history.append(
                    {
                        "case_id": req.case_id,
                        "school": req.school,
                        "max_rounds": req.max_rounds,
                        **result,
                    }
                )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("", summary="List session history")
async def list_sessions() -> list[dict]:
    return _session_history
