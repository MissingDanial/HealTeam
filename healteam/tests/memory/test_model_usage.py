from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from healteam_memory.contracts import CreateSessionCommand, SessionState, transition_snapshot
from healteam_memory.model_usage import ModelBudgetError, finish_call, reserve_call, usage_summary
from healteam_memory.training_store import Mutation, TrainingRepository


@pytest_asyncio.fixture
async def live_record(tmp_path):
    repository = TrainingRepository(tmp_path / "usage.sqlite3")
    await repository.initialize()
    created = await repository.create(
        CreateSessionCommand(client_request_id=uuid4(), case_id="teen-truancy-01", school="satir"),
        config_snapshot={"model_mode": "live", "model_max_calls": 2, "model_token_budget": 100000},
        case_snapshot={"case_id": "teen-truancy-01", "version": "1.0"},
    )
    session_id = created.record.snapshot.session_id
    await repository.transact(
        session_id,
        lambda record: Mutation(snapshot=transition_snapshot(record.snapshot, SessionState.RUNNING)),
    )
    try:
        yield repository, session_id
    finally:
        await repository.dispose()


async def reserve(repository, session_id, estimated=100, maximum=20):
    ids = []

    def change(record):
        mutation, call_id = reserve_call(
            record, purpose="family", estimated_input_tokens=estimated, max_output_tokens=maximum
        )
        ids.append(call_id)
        return mutation

    await repository.transact(session_id, change)
    return ids[0]


@pytest.mark.parametrize(
    "input_tokens,output_tokens,expected_reserved",
    [(150, None, 20), (None, 50, 100), (None, None, 120), (8, 4, 0)],
)
async def test_partial_unknown_usage_keeps_its_own_reservation(
    live_record, input_tokens, output_tokens, expected_reserved
):
    repository, session_id = live_record
    call_id = await reserve(repository, session_id)
    await repository.transact(
        session_id,
        lambda record: finish_call(
            record, call_id=call_id, input_tokens=input_tokens, output_tokens=output_tokens
        ),
    )
    usage = usage_summary(await repository.get(session_id))
    assert usage["reserved_tokens"] == expected_reserved
    assert usage["input_tokens"] == (input_tokens or 0)
    assert usage["output_tokens"] == (output_tokens or 0)
    assert usage["unknown_requests"] == int(input_tokens is None or output_tokens is None)


async def test_unsettled_call_survives_reopen_and_still_occupies_budget(live_record):
    repository, session_id = live_record
    await reserve(repository, session_id)
    await repository.dispose()
    reopened = TrainingRepository(repository.path)
    try:
        await reopened.initialize()
        usage = usage_summary(await reopened.get(session_id))
        assert usage["requests"] == usage["unknown_requests"] == 1
        assert usage["reserved_tokens"] == 120
        await reserve(reopened, session_id)
        with pytest.raises(ModelBudgetError, match="model_call_budget_exceeded"):
            await reserve(reopened, session_id)
    finally:
        await reopened.dispose()


async def test_token_budget_is_checked_before_new_reservation(live_record):
    repository, session_id = live_record
    with pytest.raises(ModelBudgetError, match="model_token_budget_exceeded"):
        await reserve(repository, session_id, estimated=100000)
    assert usage_summary(await repository.get(session_id))["requests"] == 0


async def test_settlement_is_idempotent_and_cannot_change_usage(live_record):
    repository, session_id = live_record
    call_id = await reserve(repository, session_id)

    def settle(record):
        return finish_call(record, call_id=call_id, input_tokens=8, output_tokens=4, latency_ms=3)

    first = await repository.transact(session_id, settle)
    again = await repository.transact(session_id, settle)
    assert first.record.events == again.record.events
    with pytest.raises(ValueError, match="already settled"):
        await repository.transact(
            session_id, lambda record: finish_call(record, call_id=call_id, input_tokens=9, output_tokens=4)
        )


@pytest.mark.parametrize("value", [-1, True, 1.5])
async def test_invalid_accounting_values_are_rejected_without_writes(live_record, value):
    repository, session_id = live_record
    before = await repository.get(session_id)
    with pytest.raises(ValueError):
        await reserve(repository, session_id, estimated=value)
    assert (await repository.get(session_id)).events == before.events
