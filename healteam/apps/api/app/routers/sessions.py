"""Sessions API router — SSE streaming therapy sessions."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services.session_runner import run_session

router = APIRouter()
logger = logging.getLogger("healteam.api.sessions")

# In-memory session history.
_session_history: list[dict[str, object]] = []


class SessionCreate(BaseModel):
    case_id: str = "teen-truancy-01"
    school: str = "structural"
    max_rounds: int = 10


@router.post("", summary="Create and run a therapy session (SSE stream)")
async def create_session(req: SessionCreate):
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
                    except Exception as e:
                        logger.exception("Session runner crashed")
                        yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
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
        except Exception as e:
            logger.exception("SSE stream error")
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
        finally:
            if not task.done():
                task.cancel()
            outcome = (await asyncio.gather(task, return_exceptions=True))[0]
            if isinstance(outcome, dict):
                result = outcome
            elif isinstance(outcome, Exception) and not isinstance(outcome, asyncio.CancelledError):
                logger.error("Session runner failed: %s", outcome)

            if result:
                _session_history.append({
                    "case_id": req.case_id,
                    "school": req.school,
                    "max_rounds": req.max_rounds,
                    **result,
                })

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
