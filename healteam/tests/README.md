# tests

pytest test suite for HealTeam.

**W1 status:** empty. The smoke check lives at `scripts/smoke.py` and is
not pytest-based on purpose (no LLM, no DB, no fixtures).

W2+ tasks should add:

- `tests/test_memory_schema.py` — Pydantic round-trip tests.
- `tests/test_agents.py` — `should_speak` / `respond` pure-function tests.
- `tests/test_orchestrator.py` — graph compile + topology tests.
- `tests/test_api.py` — FastAPI route tests via `httpx.AsyncClient`.
