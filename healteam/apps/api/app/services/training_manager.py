"""Single-process durable practice sessions independent from SSE readers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from math import isfinite
from typing import Any
from uuid import UUID

from healteam_memory import Actor
from healteam_memory.contracts import (
    CreateSessionCommand,
    EventEnvelope,
    EventSource,
    EventVisibility,
    ReportState,
    SessionMode,
    SessionPhase,
    SessionSnapshot,
    SessionState,
    StreamEventType,
    SubmitMessageCommand,
    transition_snapshot,
)
from healteam_memory.model_usage import ModelBudgetError, finish_call, reserve_call, usage_summary
from healteam_memory.training_store import (
    BusyError,
    ConflictError,
    EventDraft,
    Mutation,
    NotFoundError,
    RuntimeNotOwnedError,
    TrainingRecord,
    TrainingRepository,
)

from ..resources import CaseResourceError, load_validated_case, validate_supporting_resources
from ..settings import ConfigurationError, Settings
from .live_training import LiveOutputError, LiveTrainingEngine, ModelPrompt, snapshot_live_resources
from .llm import HTTPModelClient, LLMRequestError, ModelResult, build_llm_client, endpoint_fingerprint
from .mock_family import MockFamily
from .rubrics import snapshot_rubric

TERMINAL_STATES = {
    SessionState.COMPLETED,
    SessionState.CANCELLED,
    SessionState.FAILED,
    SessionState.INTERRUPTED,
}
VISIBLE = {EventVisibility.PUBLIC, EventVisibility.LEARNER}


class _GenerationTimeoutError(TimeoutError):
    """Only a generation deadline, never raw provider exception details."""


class ManagerError(RuntimeError):
    def __init__(
        self,
        code: str,
        status_code: int = 409,
        *,
        details: dict[str, Any] | None = None,
        session_id: str | UUID | None = None,
    ):
        self.code = code
        self.status_code = status_code
        self.details = dict(details or {})
        if session_id is not None:
            self.details["session_id"] = str(session_id)
        super().__init__(code)


def _snapshot_data(snapshot: SessionSnapshot, **updates: Any) -> SessionSnapshot:
    return SessionSnapshot.model_validate({**snapshot.model_dump(), **updates})


def _unchanged(record: TrainingRecord) -> Mutation:
    return Mutation(snapshot=record.snapshot, error_code=record.error_code)


def _status(snapshot: SessionSnapshot, reason: str) -> EventDraft:
    return EventDraft(
        round=snapshot.current_round,
        type=StreamEventType.STATUS_CHANGED,
        actor=Actor.SYSTEM,
        source=EventSource.SYSTEM,
        visibility=EventVisibility.PUBLIC,
        payload={"reason": reason, "snapshot": snapshot.model_dump(mode="json")},
    )


def _utterance(round_number: int, actor: Actor, content: str, **extra: Any) -> EventDraft:
    return EventDraft(
        round=round_number,
        type=StreamEventType.UTTERANCE,
        actor=actor,
        source=EventSource.HUMAN if actor == Actor.TRAINEE else EventSource.AI,
        visibility=EventVisibility.PUBLIC,
        payload={"content": content, **extra},
    )


class TrainingManager:
    def __init__(
        self,
        repository: TrainingRepository,
        *,
        settings: Settings,
        response_delay: float = 0.08,
        family: MockFamily | None = None,
        operation_timeout_seconds: float | None = None,
        stop_timeout_seconds: float | None = None,
        model_client: HTTPModelClient | None = None,
    ):
        self.repository = repository
        self.settings = settings
        self.response_delay = max(0, response_delay)
        self.family = family or MockFamily()
        self._model_client = model_client
        self.operation_timeout_seconds = self._positive_timeout(
            settings.operation_timeout_seconds
            if operation_timeout_seconds is None
            else operation_timeout_seconds
        )
        self.stop_timeout_seconds = self._positive_timeout(
            settings.stop_timeout_seconds if stop_timeout_seconds is None else stop_timeout_seconds
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._pending: dict[str, list[Callable[[str], Awaitable[None]]]] = {}
        self._worker_tokens: dict[str, object] = {}
        self._worker_context: ContextVar[tuple[str, object] | None] = ContextVar(
            f"training_worker_{id(self)}", default=None
        )
        self._generation_tasks: set[asyncio.Task[Any]] = set()
        self._retired_tasks: set[asyncio.Task[None]] = set()
        self._closing = False
        self._started = False
        self._lifecycle = 0

    @staticmethod
    def _positive_timeout(value: float) -> float:
        value = float(value)
        if not isfinite(value) or value <= 0:
            raise ValueError("training timeouts must be finite and positive")
        return value

    async def startup(self) -> None:
        await self._persist(self.repository.initialize)
        await self._persist(self.repository.recover_interrupted)
        self._lifecycle += 1
        self._started = True
        self._closing = False

    async def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        tasks = list(self._tasks.items())
        for session_id, task in tasks:
            if not task.done():
                self._retire_worker(session_id, task)
        if tasks:
            await asyncio.wait({task for _, task in tasks}, timeout=self.stop_timeout_seconds)
        # Pure generation tasks can suppress cancellation. Their results are
        # discarded and their revoked worker cannot write after this recovery.
        for task in tuple(self._generation_tasks):
            task.cancel()
        await self._persist(self.repository.recover_interrupted)

    def _check_worker(self, worker: tuple[str, object] | None) -> None:
        if worker is not None and self._worker_tokens.get(worker[0]) is not worker[1]:
            raise asyncio.CancelledError

    async def _persist(self, method: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        worker = self._worker_context.get()
        self._check_worker(worker)
        if worker is not None and method == self.repository.transact:
            mutation = args[1]

            def guarded_mutation(record: TrainingRecord) -> Mutation:
                # Check inside the transaction too: a callback waiting for the
                # SQLite writer lock may run after cancellation was accepted.
                self._check_worker(worker)
                return mutation(record)

            args = (args[0], guarded_mutation, *args[2:])
        try:
            result = await method(*args, **kwargs)
            self._check_worker(worker)
            return result
        except ManagerError:
            raise
        except ModelBudgetError:
            raise
        except NotFoundError:
            raise ManagerError("session_not_found", 404) from None
        except RuntimeNotOwnedError:
            raise ManagerError("runtime_not_owned", 503) from None
        except (BusyError, ConflictError) as exc:
            raise ManagerError(
                getattr(exc, "code", "session_conflict"), 409, session_id=getattr(exc, "session_id", None)
            ) from None
        except Exception:
            raise ManagerError("storage_unavailable", 503) from None

    def _accepting(self, lifecycle: int | None = None) -> int:
        if not self._started or self._closing:
            raise ManagerError("service_not_ready", 503)
        if lifecycle is not None and lifecycle != self._lifecycle:
            raise ManagerError("service_not_ready", 503)
        return self._lifecycle

    @staticmethod
    def _id(session_id: str | UUID) -> str:
        try:
            return str(UUID(str(session_id)))
        except (ValueError, TypeError, AttributeError):
            raise ManagerError("session_not_found", 404) from None

    async def _get(self, session_id: str | UUID) -> TrainingRecord:
        return await self._persist(self.repository.get, self._id(session_id))

    @staticmethod
    def _view(record: TrainingRecord, *, include_events: bool = True) -> dict[str, Any]:
        result = {
            **record.snapshot.model_dump(mode="json"),
            "completed_rounds": record.completed_rounds,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "error_code": record.error_code,
            "usage": usage_summary(record),
            "provider": record.config_snapshot.get("provider")
            if record.snapshot.model_mode.value == "live"
            else None,
            "model": record.config_snapshot.get("model")
            if record.snapshot.model_mode.value == "live"
            else None,
        }
        if include_events:
            result["events"] = [
                event.model_dump(mode="json") for event in record.events if event.visibility in VISIBLE
            ]
        return result

    async def snapshot(self, session_id: str | UUID) -> dict[str, Any]:
        return self._view(await self._get(session_id))

    async def list_sessions(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ManagerError("invalid_pagination", 422)
        records, total = await self._persist(self.repository.list_sessions, limit=limit, offset=offset)
        return {"items": [self._view(record, include_events=False) for record in records], "total": total}

    async def events_after(
        self, session_id: str | UUID, after_seq: int = 0
    ) -> tuple[dict[str, Any], list[EventEnvelope]]:
        if after_seq < 0:
            raise ManagerError("invalid_event_cursor", 422)
        record = await self._get(session_id)
        return self._view(record, include_events=False), [
            event for event in record.events if event.seq > after_seq and event.visibility in VISIBLE
        ]

    async def create(self, command: CreateSessionCommand) -> tuple[dict[str, Any], bool]:
        lifecycle = self._accepting()
        existing = await self._persist(self.repository.lookup_create, command)
        if existing is not None:
            return self._view(existing), False
        if self.settings.model_mode == "live":
            self._validate_live_settings()
        try:
            case = load_validated_case(command.case_id)
            validate_supporting_resources(command.school.value)
        except CaseResourceError as exc:
            raise ManagerError(exc.code, exc.status_code) from None
        settings = {
            **self.settings.public_status(),
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "timeout_seconds": self.settings.timeout_seconds,
        }
        try:
            settings["evaluation_rubric"] = snapshot_rubric(command.school.value, case)
            settings["review_max_tokens"] = self.settings.review_max_tokens
        except CaseResourceError as exc:
            raise ManagerError(exc.code, exc.status_code) from None
        if self.settings.model_mode == "live":
            try:
                settings["live_resources"] = snapshot_live_resources(command.school.value)
            except CaseResourceError as exc:
                raise ManagerError(exc.code, exc.status_code) from None
            settings.update(
                model_max_calls=self.settings.model_max_calls,
                model_token_budget=self.settings.model_token_budget,
                context_max_chars=self.settings.context_max_chars,
                send_temperature=self.settings.send_temperature,
                endpoint_fingerprint=endpoint_fingerprint(self.settings),
            )
        result = await self._persist(
            self.repository.create, command, config_snapshot=settings, case_snapshot=case
        )
        if result.replayed:
            return self._view(result.record), False
        created = not result.replayed
        session_id = str(result.record.snapshot.session_id)
        activated = False

        def activate(record: TrainingRecord) -> Mutation:
            nonlocal activated
            self._accepting(lifecycle)
            if record.snapshot.state != SessionState.CREATED:
                return _unchanged(record)
            activated = True
            state = (
                SessionState.AWAITING_INPUT
                if record.snapshot.mode == SessionMode.TRAINING
                else SessionState.RUNNING
            )
            target = transition_snapshot(record.snapshot, state)
            return Mutation(
                snapshot=target,
                events=[
                    _status(
                        target, "training_ready" if state == SessionState.AWAITING_INPUT else "demo_started"
                    )
                ],
            )

        result = await self._persist(self.repository.transact, session_id, activate)
        if activated and result.record.snapshot.mode == SessionMode.DEMO:
            self._spawn(session_id, self._run_demo, lifecycle=lifecycle)
        return self._view(result.record), created

    async def submit(self, session_id: str | UUID, command: SubmitMessageCommand) -> dict[str, Any]:
        lifecycle = self._accepting()
        session_id = self._id(session_id)

        def accept(record: TrainingRecord) -> Mutation:
            self._accepting(lifecycle)
            snapshot = record.snapshot
            if snapshot.model_mode.value == "live":
                self._validate_live_settings(record)
            if snapshot.mode != SessionMode.TRAINING:
                raise ManagerError("demo_has_no_learner_input")
            if snapshot.state != SessionState.AWAITING_INPUT:
                raise ManagerError(
                    "session_busy"
                    if snapshot.state == SessionState.RUNNING
                    else "session_not_accepting_input"
                )
            next_turn = record.completed_rounds + 1
            if command.expected_turn != next_turn:
                raise ManagerError("turn_conflict", details={"expected_turn": next_turn})
            target = _snapshot_data(
                transition_snapshot(snapshot, SessionState.RUNNING), current_round=next_turn
            )
            receipt = {
                "session_id": session_id,
                "client_message_id": str(command.client_message_id),
                "accepted_turn": next_turn,
                "state": "running",
            }
            return Mutation(
                snapshot=target,
                receipt=receipt,
                events=[
                    _utterance(
                        next_turn,
                        Actor.TRAINEE,
                        command.content,
                        client_message_id=str(command.client_message_id),
                        expected_turn=command.expected_turn,
                    ),
                    _status(target, "learner_message_accepted"),
                ],
            )

        result = await self._persist(
            self.repository.transact,
            session_id,
            accept,
            receipt_key=str(command.client_message_id),
            receipt_payload=command.model_dump(mode="json"),
        )
        if not result.replayed:
            self._spawn(session_id, lambda value: self._run_turn(value, command.content), lifecycle=lifecycle)
        return result.receipt

    async def finish(self, session_id: str | UUID) -> dict[str, Any]:
        lifecycle = self._accepting()
        session_id = self._id(session_id)

        def begin_review(record: TrainingRecord) -> Mutation:
            self._accepting(lifecycle)
            if record.snapshot.state == SessionState.COMPLETED:
                return _unchanged(record)
            if record.snapshot.model_mode.value == "live":
                self._validate_live_settings(record)
            if record.snapshot.state != SessionState.AWAITING_INPUT:
                raise ManagerError(
                    "session_busy"
                    if record.snapshot.state == SessionState.RUNNING
                    else "session_cannot_finish"
                )
            if record.completed_rounds < 1:
                raise ManagerError("round_required")
            target = transition_snapshot(
                record.snapshot, SessionState.RUNNING, phase=SessionPhase.SUPERVISION
            )
            return Mutation(snapshot=target, events=[_status(target, "review_started")])

        result = await self._persist(self.repository.transact, session_id, begin_review)
        if result.record.snapshot.state == SessionState.RUNNING:
            self._spawn(session_id, self._run_review, lifecycle=lifecycle)
        return self._view(result.record)

    async def cancel(self, session_id: str | UUID) -> dict[str, Any]:
        lifecycle = self._accepting()
        session_id = self._id(session_id)

        def request_cancel(record: TrainingRecord) -> Mutation:
            self._accepting(lifecycle)
            if record.snapshot.state in TERMINAL_STATES or record.snapshot.state == SessionState.CANCELLING:
                return _unchanged(record)
            target = transition_snapshot(
                record.snapshot, SessionState.CANCELLING, report_state=ReportState.CANCELLED
            )
            return Mutation(snapshot=target, events=[_status(target, "cancellation_requested")])

        result = await self._persist(self.repository.transact, session_id, request_cancel)
        if result.record.snapshot.state == SessionState.CANCELLING:
            task = self._tasks.get(session_id)
            if task is not None and not task.done():
                self._retire_worker(session_id, task)
                await asyncio.wait({task}, timeout=self.stop_timeout_seconds)
            await self._finalize_stop(session_id, interrupted=False)
        return await self.snapshot(session_id)

    async def report(self, session_id: str | UUID) -> dict[str, Any]:
        record = await self._get(session_id)
        if record.report is not None:
            return dict(record.report)
        return {
            "session_id": str(record.snapshot.session_id),
            "status": record.snapshot.report_state.value,
            "content": None,
            "kind": "ai_review" if record.snapshot.model_mode.value == "live" else "workflow_check",
            "generated_at": None,
            "school": record.snapshot.school.value,
            "mode": record.snapshot.mode.value,
            # Case-goal labels and examples are review material, not opening
            # hints. Expose them only with the completed saved report below.
            "rubric": None,
            "assessment": None,
        }

    async def retry_report(self, session_id: str | UUID) -> dict[str, Any]:
        lifecycle = self._accepting()
        session_id = self._id(session_id)

        def retry(record: TrainingRecord) -> Mutation:
            self._accepting(lifecycle)
            snapshot = record.snapshot
            if snapshot.model_mode.value == "live":
                self._validate_live_settings(record)
            if (
                snapshot.state not in {SessionState.FAILED, SessionState.INTERRUPTED}
                or snapshot.phase
                not in {
                    SessionPhase.SUPERVISION,
                    SessionPhase.REPORT,
                }
                or snapshot.report_state != ReportState.FAILED
            ):
                raise ManagerError("report_retry_unavailable")
            state = ReportState.GENERATING if snapshot.phase == SessionPhase.REPORT else snapshot.report_state
            target = transition_snapshot(snapshot, SessionState.RUNNING, report_state=state)
            return Mutation(snapshot=target, events=[_status(target, "report_retry_started")])

        result = await self._persist(self.repository.transact, session_id, retry)
        self._spawn(session_id, self._run_review, lifecycle=lifecycle)
        return self._view(result.record)

    def _spawn(self, session_id: str, operation: Callable[[str], Awaitable[None]], *, lifecycle: int) -> None:
        if self._closing or not self._started or lifecycle != self._lifecycle:
            return
        existing = self._tasks.get(session_id)
        if existing is not None and not existing.done():
            # The next command may commit while the prior operation is still
            # closing its transaction. Keep its accepted successor on the
            # same worker rather than dropping it or running a second worker.
            self._pending.setdefault(session_id, []).append(operation)
            return
        self._pending[session_id] = [operation]
        token = object()
        self._worker_tokens[session_id] = token
        task = asyncio.create_task(self._guarded(session_id, token), name=f"training:{session_id}")
        self._tasks[session_id] = task

        def cleanup(completed: asyncio.Task[None]) -> None:
            if not completed.cancelled():
                completed.exception()  # Consume failures even if persistence itself was unavailable.
            if self._tasks.get(session_id) is completed:
                self._tasks.pop(session_id, None)
                self._pending.pop(session_id, None)
                self._worker_tokens.pop(session_id, None)

        task.add_done_callback(cleanup)

    def _retire_worker(self, session_id: str, task: asyncio.Task[None]) -> None:
        self._worker_tokens.pop(session_id, None)
        self._pending.pop(session_id, None)
        if self._tasks.get(session_id) is task:
            self._tasks.pop(session_id, None)
        self._retired_tasks.add(task)
        task.add_done_callback(self._retired_tasks.discard)
        task.cancel()

    async def _generate(self, operation: Awaitable[Any]) -> Any:
        """Bound waiting without trusting a provider to honor cancellation.

        The detached task is only allowed to produce a value. Persistence is
        performed by the current worker after this method successfully returns.
        """
        task = asyncio.create_task(operation, name="training:generation")
        self._generation_tasks.add(task)

        def consume(completed: asyncio.Task[Any]) -> None:
            self._generation_tasks.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(consume)
        try:
            done, _ = await asyncio.wait({task}, timeout=self.operation_timeout_seconds)
            if task not in done:
                task.cancel()
                raise _GenerationTimeoutError
            return task.result()
        except asyncio.CancelledError:
            task.cancel()
            raise

    async def _guarded(self, session_id: str, token: object) -> None:
        context = self._worker_context.set((session_id, token))
        try:
            while self._pending.get(session_id):
                self._check_worker((session_id, token))
                operation = self._pending[session_id].pop(0)
                try:
                    await operation(session_id)
                except asyncio.CancelledError:
                    raise
                except _GenerationTimeoutError:
                    await self._fail(session_id, timed_out=True)
                except (LLMRequestError, LiveOutputError, ModelBudgetError, ManagerError) as exc:
                    await self._fail(session_id, error_code=exc.code)
                except Exception:
                    await self._fail(session_id)
        except asyncio.CancelledError:
            if self._worker_tokens.get(session_id) is token:
                self._pending.pop(session_id, None)
                await self._finalize_stop(session_id, interrupted=True)
        finally:
            self._worker_context.reset(context)

    async def _run_turn(self, session_id: str, learner_content: str) -> None:
        record = await self._get(session_id)
        self._assert_simulating(record, record.snapshot.current_round)
        await self._family_replies(session_id, record, learner_content)
        finished = await self._complete_round(session_id)
        if finished.snapshot.phase == SessionPhase.SUPERVISION:
            await self._run_review(session_id)

    def _validate_live_settings(self, record: TrainingRecord | None = None) -> None:
        if self.settings.model_mode != "live":
            raise ManagerError("live_configuration_changed", 503)
        try:
            self.settings.check_live_configuration()
            fingerprint = endpoint_fingerprint(self.settings)
        except ConfigurationError as exc:
            raise ManagerError(str(exc), 503) from None
        except LLMRequestError as exc:
            raise ManagerError(exc.code, 503) from None
        if record is not None and (
            record.config_snapshot.get("provider") != self.settings.provider
            or record.config_snapshot.get("model") != self.settings.model
            or record.config_snapshot.get("endpoint_fingerprint") != fingerprint
        ):
            raise ManagerError("live_configuration_changed", 503)

    @staticmethod
    def _live_engine(record: TrainingRecord) -> LiveTrainingEngine:
        return LiveTrainingEngine(
            max_tokens=int(record.config_snapshot["max_tokens"]),
            context_max_chars=int(record.config_snapshot["context_max_chars"]),
            review_max_tokens=int(record.config_snapshot["review_max_tokens"])
            if "evaluation_rubric" in record.config_snapshot
            else None,
        )

    async def _live_complete(
        self, session_id: str, purpose: str, actor: Actor | None = None
    ) -> tuple[ModelResult, ModelPrompt]:
        """Reserve first, execute only pure generation, then settle usage.

        Cancellation can leave a reservation unresolved. Its conservative
        token/call charge stays durable; late provider work never writes.
        """
        record = await self._get(session_id)
        self._validate_live_settings(record)
        prompt = self._live_engine(record).prepare(record, purpose, actor)
        call_id = ""

        def reserve(current: TrainingRecord) -> Mutation:
            nonlocal call_id
            if current.snapshot.state != SessionState.RUNNING:
                raise ManagerError("session_not_running")
            expected_phase = SessionPhase.REPORT if purpose == "review" else SessionPhase.SIMULATION
            if current.snapshot.phase != expected_phase:
                raise ManagerError("session_not_running")
            estimated = (
                len(prompt.system.encode("utf-8"))
                + sum(len(message["content"].encode("utf-8")) + 16 for message in prompt.messages)
                + 32
            )
            mutation, call_id = reserve_call(
                current,
                purpose=purpose,
                estimated_input_tokens=estimated,
                max_output_tokens=prompt.max_tokens,
            )
            return mutation

        await self._persist(self.repository.transact, session_id, reserve)
        client = self._model_client
        if client is None:
            runtime_settings = self.settings.model_copy(
                update={
                    "temperature": record.config_snapshot["temperature"],
                    "max_tokens": prompt.max_tokens,
                    "timeout_seconds": record.config_snapshot["timeout_seconds"],
                    "send_temperature": record.config_snapshot.get("send_temperature", False),
                }
            )
            client = build_llm_client(runtime_settings)
        if client is None:
            raise ManagerError("live_configuration_changed", 503)

        async def settle(result: ModelResult | LLMRequestError | None, error: str | None = None) -> None:
            await self._persist(
                self.repository.transact,
                session_id,
                lambda current: finish_call(
                    current,
                    call_id=call_id,
                    input_tokens=None if result is None else result.input_tokens,
                    output_tokens=None if result is None else result.output_tokens,
                    error_code=error,
                    latency_ms=getattr(result, "latency_ms", None),
                ),
            )

        try:
            result = await self._generate(
                client.complete(system=prompt.system, messages=prompt.messages, max_tokens=prompt.max_tokens)
            )
        except asyncio.CancelledError:
            raise
        except _GenerationTimeoutError:
            await settle(None, "report_timeout" if purpose == "review" else "response_timeout")
            raise
        except LLMRequestError as exc:
            await settle(exc, exc.code)
            raise
        except Exception:
            await settle(None, "llm_invalid_response")
            raise
        await settle(result)
        return result, prompt

    async def _family_replies(self, session_id: str, record: TrainingRecord, learner_content: str) -> None:
        round_number = record.snapshot.current_round
        for actor in self.family.actors(learner_content):
            current = await self._get(session_id)
            self._assert_simulating(current, round_number)
            if current.snapshot.model_mode.value == "live":
                engine = self._live_engine(current)
                result, _prompt = await self._live_complete(session_id, "family", actor)
                content = engine.parse_reply(result.text, actor, case=current.case_snapshot)
            else:
                content = await self._generate(
                    self.family.respond(
                        actor, record.case_snapshot, learner_content, round_number, delay=self.response_delay
                    )
                )

            def append(current: TrainingRecord, selected: Actor = actor, text: str = content) -> Mutation:
                self._assert_simulating(current, round_number)
                return Mutation(snapshot=current.snapshot, events=[_utterance(round_number, selected, text)])

            await self._persist(self.repository.transact, session_id, append)

    @staticmethod
    def _assert_simulating(record: TrainingRecord, round_number: int) -> None:
        if (
            record.snapshot.state != SessionState.RUNNING
            or record.snapshot.phase != SessionPhase.SIMULATION
            or record.snapshot.current_round != round_number
        ):
            raise ManagerError("session_not_running")

    async def _complete_round(self, session_id: str) -> TrainingRecord:
        def complete(record: TrainingRecord) -> Mutation:
            snapshot = record.snapshot
            self._assert_simulating(record, snapshot.current_round)
            completed = snapshot.current_round
            if completed >= snapshot.max_rounds:
                target = transition_snapshot(snapshot, SessionState.RUNNING, phase=SessionPhase.SUPERVISION)
                reason = "round_limit_reached"
            elif snapshot.mode == SessionMode.TRAINING:
                target = transition_snapshot(snapshot, SessionState.AWAITING_INPUT)
                reason = "waiting_for_learner"
            else:
                target, reason = snapshot, "demo_round_completed"
            return Mutation(snapshot=target, completed_rounds=completed, events=[_status(target, reason)])

        return (await self._persist(self.repository.transact, session_id, complete)).record

    async def _run_demo(self, session_id: str) -> None:
        while True:

            def begin_round(record: TrainingRecord) -> Mutation:
                self._assert_simulating(record, record.snapshot.current_round)
                number = record.completed_rounds + 1
                target = _snapshot_data(record.snapshot, current_round=number)
                events = (
                    []
                    if record.snapshot.model_mode.value == "live"
                    else [_utterance(number, Actor.DOCTOR, self.family.doctor_demo(number))]
                )
                return Mutation(snapshot=target, events=events)

            record = (await self._persist(self.repository.transact, session_id, begin_round)).record
            if record.snapshot.model_mode.value == "live":
                result, _prompt = await self._live_complete(session_id, "doctor", Actor.DOCTOR)
                content = self._live_engine(record).parse_reply(
                    result.text, Actor.DOCTOR, case=record.case_snapshot
                )

                def append_doctor(
                    current: TrainingRecord, number: int = record.snapshot.current_round, text: str = content
                ) -> Mutation:
                    self._assert_simulating(current, number)
                    return Mutation(
                        snapshot=current.snapshot,
                        events=[_utterance(current.snapshot.current_round, Actor.DOCTOR, text)],
                    )

                record = (await self._persist(self.repository.transact, session_id, append_doctor)).record
            await self._family_replies(session_id, record, "请爸爸、妈妈和孩子分别说一件刚才注意到的事")
            record = await self._complete_round(session_id)
            if record.snapshot.phase == SessionPhase.SUPERVISION:
                await self._run_review(session_id)
                return

    async def _run_review(self, session_id: str) -> None:
        record = await self._get(session_id)
        if record.snapshot.state != SessionState.RUNNING or record.snapshot.phase not in {
            SessionPhase.SUPERVISION,
            SessionPhase.REPORT,
        }:
            raise ManagerError("review_not_running")
        if record.snapshot.phase == SessionPhase.SUPERVISION:
            await self._generate(asyncio.sleep(self.response_delay))

            def review(current: TrainingRecord) -> Mutation:
                if (
                    current.snapshot.state != SessionState.RUNNING
                    or current.snapshot.phase != SessionPhase.SUPERVISION
                ):
                    raise ManagerError("review_not_running")
                target = transition_snapshot(
                    current.snapshot,
                    SessionState.RUNNING,
                    phase=SessionPhase.REPORT,
                    report_state=ReportState.GENERATING,
                )
                return Mutation(snapshot=target, events=[_status(target, "workflow_review_finished")])

            record = (await self._persist(self.repository.transact, session_id, review)).record
        if record.snapshot.model_mode.value == "live":
            result, prompt = await self._live_complete(session_id, "review")
            engine = self._live_engine(record)
            structured = engine.parse_review(result.text, prompt)
            assessment = structured.pop("assessment", None)
            report = {
                "session_id": str(record.snapshot.session_id),
                "status": "ready",
                "content": engine.review_markdown(structured, demo=record.snapshot.mode == SessionMode.DEMO),
                "structured": structured,
                "assessment": assessment,
                "rubric": record.config_snapshot.get("evaluation_rubric"),
                "kind": "ai_review",
                "generated_at": datetime.now(UTC).isoformat(),
                "school": record.snapshot.school.value,
                "mode": record.snapshot.mode.value,
            }
        else:
            report = await self._generate(self._generate_report(record))

        def commit(current: TrainingRecord) -> Mutation:
            if (
                current.snapshot.state != SessionState.RUNNING
                or current.snapshot.phase != SessionPhase.REPORT
            ):
                raise ManagerError("report_not_running")
            target = transition_snapshot(
                current.snapshot, SessionState.COMPLETED, report_state=ReportState.READY
            )
            event = EventDraft(
                round=target.current_round,
                type=StreamEventType.REPORT_READY,
                actor=Actor.SYSTEM,
                source=EventSource.SYSTEM,
                visibility=EventVisibility.PUBLIC,
                payload=dict(report),
            )
            return Mutation(snapshot=target, report=report, events=[event, _status(target, "report_saved")])

        await self._persist(self.repository.transact, session_id, commit)

    async def _generate_report(self, record: TrainingRecord) -> dict[str, Any]:
        await asyncio.sleep(self.response_delay)
        snapshot = record.snapshot
        training = snapshot.mode == SessionMode.TRAINING
        title = "离线训练流程验证报告" if training else "离线自动示范流程报告（非学员成绩）"
        content = [
            f"# {title}",
            "",
            "> 固定模板与确定性回应，仅用于验证软件流程；不代表专业仿真、治疗建议或学员能力评价。",
            "",
            f"- 模式：{'学员输入流程训练' if training else '自动示范，非学员成绩'}",
            f"- 学派选择：{snapshot.school.value}（仅记录选择，不进行专业评分）",
            f"- 已完成轮次：{record.completed_rounds}",
            "",
            "## 已保存的输入与回应证据",
            "",
        ]
        for event in record.events:
            if event.type == StreamEventType.UTTERANCE:
                excerpt = " ".join(event.payload["content"].split())[:300]
                content.append(f"- 第 {event.round} 轮 · {event.actor.value} · 事件 #{event.seq}：{excerpt}")
        content.extend(
            [
                "",
                "## 流程说明",
                "",
                "本报告只核对输入、回应、轮次和保存过程，没有专业评分或治疗效果结论。",
                "以上证据可在会话记录中按事件序号查阅。",
            ]
        )
        return {
            "session_id": str(snapshot.session_id),
            "status": "ready",
            "content": "\n".join(content),
            "kind": "workflow_check",
            "rubric": record.config_snapshot.get("evaluation_rubric"),
            "assessment": None,
            "generated_at": datetime.now(UTC).isoformat(),
            "school": snapshot.school.value,
            "mode": snapshot.mode.value,
        }

    async def _fail(self, session_id: str, *, timed_out: bool = False, error_code: str | None = None) -> None:
        def fail(record: TrainingRecord) -> Mutation:
            if record.snapshot.state in TERMINAL_STATES or record.snapshot.state == SessionState.CANCELLING:
                return _unchanged(record)
            reviewing = record.snapshot.phase in {SessionPhase.SUPERVISION, SessionPhase.REPORT}
            code = error_code or (
                ("report_timeout" if reviewing else "response_timeout")
                if timed_out
                else ("report_failed" if reviewing else "response_failed")
            )
            target = transition_snapshot(
                record.snapshot,
                SessionState.FAILED,
                report_state=ReportState.FAILED if reviewing else record.snapshot.report_state,
            )
            error = EventDraft(
                round=target.current_round,
                type=StreamEventType.ERROR,
                actor=Actor.SYSTEM,
                source=EventSource.SYSTEM,
                visibility=EventVisibility.PUBLIC,
                payload={
                    "code": code,
                    "message": "生成等待超时，已保存的记录仍可查看。"
                    if timed_out
                    else "生成流程未完成，已保存的记录仍可查看。",
                },
            )
            return Mutation(snapshot=target, error_code=code, events=[error, _status(target, code)])

        await self._persist(self.repository.transact, session_id, fail)

    async def _finalize_stop(self, session_id: str, *, interrupted: bool) -> None:
        def stop(record: TrainingRecord) -> Mutation:
            if record.snapshot.state in TERMINAL_STATES:
                return _unchanged(record)
            if interrupted:
                target = transition_snapshot(
                    record.snapshot,
                    SessionState.INTERRUPTED,
                    report_state=ReportState.FAILED
                    if record.snapshot.phase != SessionPhase.SIMULATION
                    else record.snapshot.report_state,
                )
                code = "process_interrupted"
            else:
                if record.snapshot.state != SessionState.CANCELLING:
                    return _unchanged(record)
                target = transition_snapshot(record.snapshot, SessionState.CANCELLED)
                code = None
            return Mutation(
                snapshot=target,
                error_code=code,
                events=[_status(target, "process_interrupted" if interrupted else "cancelled")],
            )

        await self._persist(self.repository.transact, session_id, stop)
