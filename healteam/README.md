# HealTeam

> **家庭治疗 AI 陪练系统** — 5 个 AI Agent 协作，让心理学学员低成本、高保真地练习家庭治疗干预技能。
>
> **当前状态**：架构骨架、内容资产与基础联调能力已落盘，**82 个 pytest 全过**。
> 但**真实行为**（家庭参与、LLM 调用、旁路提示、督导会）**在 W2**。详见 [`docs/w1-integration-report.md`](./docs/w1-integration-report.md) §0 修订说明。

See [`docs/architecture.md`](./docs/architecture.md) for the full architecture, contracts, and decision log.
See [`docs/w1-integration-report.md`](./docs/w1-integration-report.md) for the **honest** W1 delivery report.
See [`docs/w1-issues.md`](./docs/w1-issues.md) for outstanding work.

---

## Repository layout

```
healteam/
├── apps/
│   ├── api/                FastAPI backend (W1: stubs only)
│   └── web/                Next.js 14 frontend (W1: landing page only)
├── packages/
│   ├── agents/             BaseAgent Protocol + 6 agent stubs + LangGraph orchestrator
│   ├── memory/             Shared memory Pydantic models + store interface
│   └── prompts/            Prompt templates (W1: empty; populated by agent-prompts task)
├── data/
│   ├── cases/              Case JSON (W1: empty; populated by case-content task)
│   └── schools/            School templates (W1: empty; populated by school-templates task)
├── tests/                  Test suite (W1: empty)
├── scripts/                Operational scripts (smoke.py lives here)
├── docs/                   Architecture & decisions
├── docker-compose.yml
├── .env.example
└── README.md               (this file)
```

---

## Quick start

### Prerequisites

- Docker + Docker Compose v2
- (Optional, for local Python dev) Python 3.11+, Poetry
- (Optional, for local Node dev) Node 20+, pnpm

### 1. Clone & enter

```bash
git clone <repo-url> healteam
cd healteam
```

### 2. Configure env

```bash
cp .env.example .env
# Edit .env — at minimum set OPENAI_API_KEY (or leave blank for W1 smoke)
```

### 3. Bring up the stack

```bash
docker compose up --build
```

You should see:

- `healteam-postgres` on `localhost:5432` (healthy after ~5s)
- `healteam-api` on `http://localhost:8000` (healthy after the API builds)
- `healteam-web` on `http://localhost:3000`

### 4. Verify

```bash
# Backend health
curl http://localhost:8000/healthz
# → {"status":"ok"}

# Backend readiness
curl http://localhost:8000/readyz
# → {"status":"ready"}

# API agents listing
curl http://localhost:8000/api/v1/agents
# → [ {name: "doctor", ...}, ... ]

# Web landing page
open http://localhost:3000
```

### 5. Smoke (no Docker, no LLM)

```bash
# From the repo root
python scripts/smoke.py
# → exit 0; verifies imports + session construction + LangGraph compile
```

---

## What works in W1

- [x] `docker compose up` boots the full stack.
- [x] `GET /healthz` and `GET /readyz` respond.
- [x] `GET /api/v1/agents` lists the 6 agents.
- [x] `GET /api/v1/sessions` returns `[]`; `POST /api/v1/sessions` returns a placeholder.
- [x] `GET /api/v1/reports/{id}` returns 501 (intentional — W2 work).
- [x] Web landing page renders the agent list and links to the API.
- [x] `python scripts/smoke.py` exits 0 without LLM, DB, or network.

## What is stubbed (W2 tasks)

- LLM calls — only `StubLLMClient` is in place. The `integration-verify` task swaps in an OpenAI-compatible client.
- Case / school JSON — directories exist but are empty; `case-content` and `school-templates` tasks populate them.
- Prompt content — `packages/prompts/*.md` files don't exist yet; `agent-prompts` task writes them.
- Persistence — `InMemoryStore` only; `persistence` task adds Postgres + pgvector.
- End-to-end therapy flow — the orchestrator compiles and runs in-memory; the live WebSocket + supervision meeting is a W2 task.

See [`docs/architecture.md`](./docs/architecture.md) §9 for the full deferral list.

---

## Local development (without Docker)

### Backend

```bash
cd apps/api
poetry install
poetry run uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd apps/web
corepack enable   # one-time
pnpm install      # or: npm install
pnpm dev          # or: npm run dev
```

### Monorepo packages

Both `apps/api/pyproject.toml` reference `../../packages/{memory,agents}` as path
dependencies. After `poetry install` they are editable installs, so any edit
in `packages/agents/orchestrator.py` is immediately visible to the API.

---

## License

MIT. See [`../LICENSE`](../LICENSE).
