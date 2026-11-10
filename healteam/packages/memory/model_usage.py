"""Durable per-session call reservations, including uncertain provider results.

No request/response bodies, credentials or endpoint URLs enter accounting.
Token reservations are estimates for local budget control, not billing data.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .contracts import EventSource, EventVisibility, ModelMode, SessionState, StreamEventType, UsageSummary
from .schema import Actor
from .training_store import EventDraft, Mutation, TrainingRecord


class ModelBudgetError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _calls(record: TrainingRecord) -> tuple[dict[str, dict], dict[str, dict]]:
    starts, finishes = {}, {}
    for event in record.events:
        if event.type == StreamEventType.MODEL_CALL_STARTED:
            starts[event.payload["call_id"]] = event.payload
        elif event.type == StreamEventType.MODEL_CALL_FINISHED:
            finishes[event.payload["call_id"]] = event.payload
    return starts, finishes


def usage_summary(record: TrainingRecord) -> dict[str, int]:
    starts, finishes = _calls(record)
    known_input = known_output = reserved = unknown = 0
    for call_id, start in starts.items():
        finish = finishes.get(call_id, {})
        input_tokens, output_tokens = finish.get("input_tokens"), finish.get("output_tokens")
        known_input += input_tokens or 0
        known_output += output_tokens or 0
        if input_tokens is None or output_tokens is None:
            unknown += 1
            estimated_input = start["reserved_tokens"] - start["max_output_tokens"]
            reserved += estimated_input if input_tokens is None else 0
            reserved += start["max_output_tokens"] if output_tokens is None else 0
    live = record.snapshot.model_mode == ModelMode.LIVE
    return UsageSummary(
        requests=len(starts),
        input_tokens=known_input,
        output_tokens=known_output,
        unknown_requests=unknown,
        reserved_tokens=reserved,
        max_calls=record.config_snapshot.get("model_max_calls", 128) if live else 0,
        token_budget=record.config_snapshot.get("model_token_budget", 250000) if live else 0,
    ).model_dump()


def _event(record: TrainingRecord, event_type: StreamEventType, payload: dict[str, Any]) -> EventDraft:
    return EventDraft(
        round=record.snapshot.current_round,
        type=event_type,
        actor=Actor.SYSTEM,
        source=EventSource.SYSTEM,
        visibility=EventVisibility.INTERNAL,
        payload=payload,
    )


def reserve_call(
    record: TrainingRecord, *, purpose: str, estimated_input_tokens: int, max_output_tokens: int
) -> tuple[Mutation, str]:
    if record.snapshot.model_mode != ModelMode.LIVE:
        raise ValueError("Only live sessions can reserve model calls")
    if record.snapshot.state != SessionState.RUNNING:
        raise ValueError("Only running sessions can reserve model calls")
    if purpose not in {"family", "doctor", "review"}:
        raise ValueError("Unknown model call purpose")
    for value in (estimated_input_tokens, max_output_tokens):
        if type(value) is not int or value < 0:
            raise ValueError("Token reservations must be nonnegative integers")
    if max_output_tokens < 1:
        raise ValueError("Output reservation must be positive")
    summary = usage_summary(record)
    if summary["requests"] >= summary["max_calls"]:
        raise ModelBudgetError("model_call_budget_exceeded")
    reserved = estimated_input_tokens + max_output_tokens
    committed = summary["input_tokens"] + summary["output_tokens"] + summary["reserved_tokens"]
    if committed + reserved > summary["token_budget"]:
        raise ModelBudgetError("model_token_budget_exceeded")
    call_id = str(uuid4())
    event = _event(
        record,
        StreamEventType.MODEL_CALL_STARTED,
        {
            "call_id": call_id,
            "purpose": purpose,
            "reserved_tokens": reserved,
            "max_output_tokens": max_output_tokens,
        },
    )
    return Mutation(snapshot=record.snapshot, error_code=record.error_code, events=[event]), call_id


def finish_call(
    record: TrainingRecord,
    *,
    call_id: str,
    input_tokens: int | None,
    output_tokens: int | None,
    error_code: str | None = None,
    latency_ms: int | None = None,
) -> Mutation:
    for value in (input_tokens, output_tokens):
        if value is not None and (type(value) is not int or not 0 <= value <= 2**31 - 1):
            raise ValueError("Usage must be a nonnegative integer or unknown")
    if latency_ms is not None and (type(latency_ms) is not int or latency_ms < 0):
        raise ValueError("Latency must be a nonnegative integer or unknown")
    starts, finishes = _calls(record)
    if call_id not in starts:
        raise ValueError("Model call was never reserved")
    payload = {
        "call_id": call_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "error_code": error_code,
        "latency_ms": latency_ms,
    }
    if call_id in finishes:
        if finishes[call_id] != payload:
            raise ValueError("Model call already settled with different usage")
        return Mutation(snapshot=record.snapshot, error_code=record.error_code)
    return Mutation(
        snapshot=record.snapshot,
        error_code=record.error_code,
        events=[_event(record, StreamEventType.MODEL_CALL_FINISHED, payload)],
    )


__all__ = ["ModelBudgetError", "reserve_call", "finish_call", "usage_summary"]
