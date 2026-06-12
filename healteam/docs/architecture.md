# HealTeam — Architecture (W1 Foundation)

> **Status:** Foundation document. All subsequent tasks (case data, agent prompts, orchestrator wiring, API endpoints, UI) **must conform to the contracts defined here**.
>
> **Owners:** arch-foundation task (this task). Anything that contradicts this document is a bug; raise it on the team board.

---

## 1. Tech Stack (locked)

| Layer | Choice | Reason (locked decision) |
|---|---|---|
| Agent orchestration | **LangGraph** (Python) | State machine semantics map cleanly to the "round-based therapy session" + 5-speaker routing. Lighter weight than AutoGen's group-chat. Easier to debug than CrewAI. See §7. |
| Backend | **FastAPI** on Python 3.11+ | Async-friendly, Pydantic-native, plays well with LangGraph (same Python process). |
| Frontend | **Next.js 14+ (App Router) + Tailwind CSS** | App Router for streaming/SSR during long therapy sessions; Tailwind for fast iteration. |
| Database | **PostgreSQL 16 + pgvector** | Single store for both structured session data (events, emotions, relationships) and semantic retrieval (experience library, RAG over school theory). |
| Container | **Docker Compose** (MVP) | One-command boot for graders. K8s is v1.0+ scope. |
| LLM | **OpenAI-compatible protocol** abstracted via env vars | Default model selectable; provider-agnostic. Lets us swap to DeepSeek / Tongyi / Anthropic without code change. |
| Frontend pkg mgr | **pnpm** | Faster, deterministic, monorepo-friendly. |
| Backend pkg mgr | **poetry** | Lockfile + dependency groups; pairs cleanly with FastAPI + LangChain/LangGraph. |

### 1.1 Locked version pins

```
python  = ">=3.11,<3.13"
node    = ">=20"
postgres = 16  (with pgvector >= 0.7)
langgraph >= 0.2
fastapi  >= 0.110
pydantic >= 2.6
next     >= 14.2
```

---

## 2. System Architecture

### 2.1 Container-level view

```
┌─────────────────────────────────────────────────────────────┐
│                       docker compose                         │
│                                                              │
│  ┌──────────┐  WebSocket/HTTP  ┌──────────┐    SQL/vector   │
│  │   web    │ ◄──────────────► │   api    │ ◄────────────┐  │
│  │ Next.js  │                  │ FastAPI  │              │  │
│  │ :3000    │                  │ :8000    │              │  │
│  └──────────┘                  └────┬─────┘              │  │
│                                     │                     │  │
│                          ┌──────────▼──────────┐          │  │
│                          │ LangGraph runtime   │          │  │
│                          │  (in-process)        │          │  │
│                          │                      │          │  │
│                          │  ┌────────────────┐  │          │  │
│                          │  │  Orchestrator   │  │          │  │
│                          │  │   StateGraph    │  │          │  │
│                          │  └─────┬──────────┘  │          │  │
│                          │        │ delegates   │          │  │
│                          │  ┌─────▼──────────┐  │          │  │
│                          │  │ 5 BaseAgents   │  │          │  │
│                          │  │ doctor / psych │  │          │  │
│                          │  │ observer /     │  │          │  │
│                          │  │ father / mother│  │          │  │
│                          │  │ / patient      │  │          │  │
│                          │  └─────┬──────────┘  │          │  │
│                          │        │ read/write  │          │  │
│                          │  ┌─────▼──────────┐  │          │  │
│                          │  │ memory store   │──┼──────────┼──┘
│                          │  │ + experience   │  │          │
│                          │  └────────────────┘  │          │  │
│                          └──────────────────────┘          │  │
│                                                            │  │
│                          ┌──────────────────────┐          │  │
│                          │  postgres:16         │ ◄────────┘
│                          │  + pgvector          │
│                          │  :5432               │
│                          └──────────────────────┘
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Module-level view (Python)

```
healteam/
├── apps/api/                       FastAPI process (HTTP/WS entry)
│   └── app/
│       ├── main.py                 FastAPI app factory
│       ├── routers/                HTTP routes (sessions, reports) — STUBS in W1
│       └── services/               Service layer that wraps orchestrator
│
├── packages/
│   ├── agents/                     LangGraph + 6 agents (5+1 模型)
│   │   ├── base.py                 BaseAgent Protocol (contract — §4)
│   │   ├── doctor.py               AI 主治医师
│   │   ├── psychiatrist.py         AI 心理医生
│   │   ├── observer.py             AI 旁观者 (L1 evolution engine lives here)
│   │   ├── father.py               AI 父亲
│   │   ├── mother.py               AI 母亲
│   │   ├── patient.py              AI 病人 (青少年)
│   │   └── orchestrator.py         LangGraph StateGraph (skeleton — §5)
│   │
│   ├── memory/                     Shared memory + experience library
│   │   ├── schema.py               Pydantic models (contract — §3)
│   │   ├── store.py                Postgres + pgvector adapter (stub in W1)
│   │   └── experience.py           Experience library helpers (stub in W1)
│   │
│   └── prompts/                    Prompt templates (text files, code-agnostic)
│       └── README.md               Placeholder — populated by agent-prompts task
│
├── data/
│   ├── cases/                      Case JSON files (populated by case-content task)
│   └── schools/                    School templates (populated by school-templates task)
│
├── tests/                          Test suite (pytest)
├── scripts/                        Operational scripts (smoke, db init, etc.)
└── docs/                           Architecture & decisions (this file)
```

---

## 3. Shared Memory Schema (locked)

> All session state is exchanged between agents and persisted to Postgres via these Pydantic models.
> Defined in `packages/memory/schema.py`. **Other tasks must not redefine these — import them.**

### 3.1 High-level shape

```python
Session
├── id: str                          # UUID
├── case_id: str                     # e.g. "teen_truancy_01"
├── school: School                   # enum: structural | bowen | satir
├── round: int                       # current round (0 = not started)
├── status: SessionStatus            # enum: pending | running | ended | reported
├── created_at: datetime
├── updated_at: datetime
│
├── events: list[Event]              # ordered chronological log
├── emotions: EmotionMap             # actor -> emotion_label snapshot
├── relationships: RelationshipMap   # pair -> relation_type snapshot
├── interventions: list[Intervention]
│
└── experience_refs: list[str]       # Experience.pattern_id used/learned
```

### 3.2 Field reference (must match schema.py)

| Model | Field | Type | Notes |
|---|---|---|---|
| **Session** | `id` | `str` (UUID4) | Primary key |
| | `case_id` | `str` | References `data/cases/*.json` |
| | `school` | `School` (enum) | Locked at session start |
| | `round` | `int` (≥0) | Incremented per therapy turn |
| | `status` | `SessionStatus` (enum) | State machine of session lifecycle |
| | `created_at` / `updated_at` | `datetime` (UTC) | |
| | `events` | `list[Event]` | Append-only log |
| | `emotions` | `EmotionMap` | Latest snapshot, not historical |
| | `relationships` | `RelationshipMap` | Latest snapshot |
| | `interventions` | `list[Intervention]` | Append-only log |
| | `experience_refs` | `list[str]` | Foreign refs to `Experience.pattern_id` |
| **Event** | `round` | `int` | Round at which event was emitted |
| | `actor` | `Actor` (enum) | doctor / psychiatrist / observer / father / mother / patient / trainee / system |
| | `type` | `EventType` (enum) | utterance / defense / intervention / hint / system / meta |
| | `content` | `str` | The actual text or payload description |
| | `tags` | `list[str]` | Free-form: ["rationalization", "alliance_with_mother", ...] |
| | `timestamp` | `datetime` (UTC) | |
| **EmotionMap** | (root) | `dict[Actor, str]` | e.g. `{"father": "tense", "mother": "anxious"}` |
| **RelationshipMap** | (root) | `dict[str, RelationType]` | Key format: `"<actor_a>__<actor_b>"` (sorted) |
| **Intervention** | `round` | `int` | |
| | `by` | `Actor` | Typically `trainee` or `doctor` |
| | `type` | `str` | e.g. "reframing", "genogram", "circular_question" |
| | `effectiveness` | `Effectiveness` (enum) | pending / positive / negative / neutral |
| | `notes` | `str` | Free-form |
| **Experience** | `pattern_id` | `str` | Stable id, e.g. `exp_struct_avoidant_patient_001` |
| | `school` | `School` | |
| | `condition` | `str` | When this pattern applies: "patient shows withdrawal for 3+ rounds" |
| | `hint` | `str` | Suggested action / observation |
| | `weight` | `float` | 0.0..1.0, updated by L1 evolution (observer) |
| | `created_at` / `updated_at` | `datetime` (UTC) | |
| | `embedding` | `list[float]` (optional) | pgvector embedding for semantic retrieval |

### 3.3 Enums (locked vocabulary)

```
School          = structural | bowen | satir
SessionStatus   = pending | running | ended | reported
Actor           = doctor | psychiatrist | observer | father | mother | patient | trainee | system
EventType       = utterance | defense | intervention | hint | system | meta
RelationType    = alliance | confrontation | neutral | coalition
Effectiveness   = pending | positive | negative | neutral
```

> **Rule:** if a new concept needs a new enum value, add it to `schema.py` first, then reference it downstream. Do not sprinkle string literals.

---

## 4. Agent Contract (locked)

> All 6 agent modules must satisfy `BaseAgent`. Defined in `packages/agents/base.py`.
>
> **5+1 Agent 分类**: 5 个治疗室 Agent(doctor 主治医师 + observer 旁观者 + father/mother/patient 家庭) + 1 个督导会专属 psychiatrist(不在治疗室发言)。单一事实来源见 [`agent-model.md`](./agent-model.md)。工作流见 [`workflow.md`](./workflow.md)。

### 4.1 Protocol

```python
class BaseAgent(Protocol):
    name: str                                          # e.g. "doctor"
    role: str                                          # e.g. "AI 主治医师"
    system_prompt_path: str                            # path to .md file under packages/prompts/

    def respond(self, context: SessionContext) -> str: ...
    def should_speak(self, context: SessionContext) -> bool: ...
    def observe(self, context: SessionContext) -> Optional[Hint]: ...   # observer-only; default NotImplemented
```

### 4.2 `SessionContext` (the read-only view agents see)

```python
@dataclass
class SessionContext:
    session: Session                  # full memory snapshot
    last_event: Optional[Event]       # most recent event, for quick "what just happened"
    speaker_candidates: list[Actor]   # who is allowed to speak this turn (orchestrator decides)
    observer_hint: Optional[Hint]     # most recent hint from observer (doctor-only by default)
```

> **Why a context object and not raw Session?** It freezes the agent's view of the world for one turn, prevents agents from mutating shared state, and makes them trivial to unit-test (just construct a `SessionContext` with a fake session).

### 4.3 `Hint` (observer → doctor side-channel)

```python
@dataclass
class Hint:
    round: int
    trigger: str                      # e.g. "defense_escalation", "stagnation_3_rounds"
    content: str                      # the earpiece prompt
    confidence: float                 # 0.0..1.0
    related_experience: Optional[str] # Experience.pattern_id, if any
```

### 4.4 Rules agents must follow

1. **No direct memory writes.** Agents return text/hints; the orchestrator is the only writer of `Session.events`, `EmotionMap`, `RelationshipMap`, `Intervention`.
2. **Prompt-driven personality.** All persona/rules live in the `.md` file pointed to by `system_prompt_path`. Code in this repo never hardcodes "you are a calm therapist" — that lives in the prompt file (W2 task: `agent-prompts`).
3. **Determinism where possible.** `should_speak` must be a pure function of `SessionContext` (no LLM call). The orchestrator may call it for all 6 agents each turn to decide routing.
4. **Idempotent construction.** `DoctorAgent()`, `FatherAgent()` etc. must be safe to instantiate without an LLM client — the client is injected at runtime via a setter, never at import time.

---

## 5. Orchestrator (LangGraph) Skeleton

> Defined in `packages/agents/orchestrator.py`. W1 only ships the **shape** — actual LLM calls are wired in the `integration-verify` task.

### 5.1 State

```python
class TherapyState(TypedDict):
    session: Session
    last_event: Optional[Event]
    pending_speaker: Optional[Actor]
    observer_hint: Optional[Hint]
    round: int
    is_terminated: bool
    termination_reason: Optional[str]    # "max_rounds" | "user_quit" | "natural_close"
```

### 5.2 Graph topology

```
        ┌──────────────┐
        │  load_case   │   (one-shot, seeds Session from case JSON + school template)
        └──────┬───────┘
               ▼
        ┌──────────────┐
   ┌───►│decide_speaker│  (calls should_speak on all 5; picks next actor)
   │    └──────┬───────┘
   │           ▼
   │    ┌──────────────┐
   │    │ agent_respond │  (calls respond() on the chosen agent)
   │    └──────┬───────┘
   │           ▼
   │    ┌──────────────┐
   │    │ observer_hint│  (observer.observe() → Hint; side-channel to doctor)
   │    └──────┬───────┘
   │           ▼
   │    ┌──────────────┐
   │    │update_memory │  (append Event; refresh EmotionMap / RelationshipMap)
   │    └──────┬───────┘
   │           ▼
   │    ┌──────────────┐
   │    │  check_end   │  (round ≥ 20 OR natural close → end; else → back to decide_speaker)
   │    └──────┬───────┘
   │           ▼ (end)
   │    ┌──────────────┐
   └────┤     END      │
        └──────────────┘
```

### 5.3 Conditional edges (skeleton)

- `decide_speaker` → `agent_respond` (always)
- `agent_respond` → `observer_hint` (always, observer runs every turn)
- `observer_hint` → `update_memory` (always)
- `update_memory` → `check_end` (always)
- `check_end` → `decide_speaker` (loop) **OR** `END` (terminal)

### 5.4 What the W1 skeleton MUST do

- Compile successfully (`graph.compile()` returns without error).
- Be runnable end-to-end with stub agents that return placeholder strings — no LLM call.
- Expose `Orchestrator().run(session_id)` as the public entry point.
- Persist nothing (in-memory only in W1; persistence is a later task).

What it MUST NOT do yet:
- Call any LLM.
- Read/write Postgres.
- Load case JSON from `data/cases/` (the loader is a stub in W1).

---

## 6. Data Flow (single therapy turn)

```
trainee input (or auto-pilot in MVP)
        │
        ▼
[api/WS] receives message
        │
        ▼
[orchestrator.dispatch]   loads TherapyState
        │
        ├─► decide_speaker  ──► ask all 6 agents: should_speak(ctx)?
        │                            (note: psychiatrist 永 False in live session; 督导会子图显式调用)
        │                            │
        │                            ▼
        │                     pick highest-priority candidate
        │
        ├─► agent_respond   ──► chosen_agent.respond(ctx)  →  text
        │
        ├─► observer_hint   ──► observer.observe(ctx)      →  Hint | None
        │
        ├─► update_memory   ──► append Event to session.events
        │                       refresh EmotionMap / RelationshipMap
        │                       record Intervention if any
        │
        ├─► check_end       ──► round ≥ MAX_ROUNDS ?
        │                       ├─ no  → loop
        │                       └─ yes → END
        │
        ▼
[api/WS] streams Event back to web
        │
        ▼
[web] updates UI, shows hint to trainee only (side-channel)
```

### 6.1 End-of-session flow (out of scope for W1 skeleton, sketched here for context)

```
session ends
   │
   ▼
supervision_meeting (3 agents: observer, psychiatrist, doctor converse 3–5 rounds)
   │
   ▼
supervision_consensus (text)
   │
   ▼
report_generator (structured markdown / PDF)
   │
   ▼
experience_lib.update (weights on successful / failed patterns)
   │
   ▼
return report to trainee
```

---

## 7. Key Decisions & Rationale

### 7.1 Why LangGraph over AutoGen / CrewAI

- **Explicit state machine**: a therapy session *is* a state machine (`round`, `speaker`, `phase`). LangGraph's `StateGraph` is a 1:1 fit. AutoGen's group-chat is implicit and hard to inspect.
- **Deterministic routing**: we need `decide_speaker` to be a pure function of session state. LangGraph makes this explicit; AutoGen tends to rely on the LLM to pick the next speaker.
- **Side-channel support**: the observer's hint to the doctor is a first-class concern in our design. LangGraph's parallel node + reducer pattern handles it cleanly.
- **Python-native & async-friendly**: same process as FastAPI, no IPC.

### 7.2 Why Pydantic v2 for shared memory

- Type-checked at runtime, which catches agent bugs early.
- JSON-serializable → trivial to persist as JSONB in Postgres.
- `model_dump()` / `model_validate()` make round-trips with the DB painless.

### 7.3 Why Postgres + pgvector (not a separate vector DB)

- One store, one backup story, one auth model.
- The MVP volume of vectors is tiny (single-digit thousands of experience entries). Pinecone / Weaviate is over-engineering for W1.
- If we outgrow it in v0.3, the `memory.store.MemoryStore` interface is a clean swap point.

### 7.4 Why in-process agents (not a separate agent service)

- Latency: round-based dialogue has tight loops; HTTP between agent and orchestrator adds 5–20ms per hop and complicates streaming.
- Deployment simplicity: one FastAPI container to operate.
- Tradeoff accepted: agents share the API process's CPU/memory. If we ever need independent scaling, the `BaseAgent` interface is the seam to extract.

### 7.5 Why prompt templates live in `packages/prompts/*.md` (not in code)

- Therapists + AI safety reviewers need to read and edit prompts without touching Python.
- Lets us version prompts in git diffs that are actually readable.
- Decouples the `agent-prompts` task (W2) from the `arch-foundation` task (W1) — they can run in parallel.

### 7.6 Why no real LLM call in W1

- Faster iteration on contracts — every change to a Pydantic field or graph edge should compile and run within seconds, not minutes waiting on an LLM.
- Lets `smoke.py` run in CI without API keys.
- Forces us to keep LLM calls behind interfaces (the `LLMClient` protocol, defined in `apps/api/app/services/llm.py` in W1) so swap-in is one-line in W2.

---

## 8. Cross-Task Contracts (what every other task must follow)

| Contract | Defined in | Other tasks must |
|---|---|---|
| Shared memory models | `packages/memory/schema.py` | Import, not re-define. Add new enums here first. |
| `BaseAgent` Protocol | `packages/agents/base.py` | Conform all 6 agents to it. |
| `SessionContext` shape | `packages/agents/base.py` | Read-only view; agents don't see raw `Session` writes mid-turn. |
| `TherapyState` shape | `packages/agents/orchestrator.py` | Other tasks add fields here, never redefine. |
| `Actor` / `EventType` enums | `packages/memory/schema.py` | Use these names verbatim, not free-form strings. |
| LLM client interface | `apps/api/app/services/llm.py` | All LLM calls go through it; no direct `openai.OpenAI()` calls. |
| HTTP API surface | `apps/api/app/routers/*.py` | Routers are thin: validate → call service → return. No business logic in routers. |
| Frontend data shapes | `apps/web/app/lib/types.ts` | Mirror the Pydantic models 1:1 (the `agent-prompts` and `api-impl` tasks will populate). |

### 8.1 Breaking-change protocol

If a downstream task needs to change a contract, the change MUST:
1. Be raised on the team board with the tag `contract-change`.
2. Update this document in the same commit as the code change.
3. Be reviewed by at least one other worker agent before merge.

---

## 9. What this task does NOT decide (deferred)

- Specific prompt content for the 6 agents → `agent-prompts` task (W2). 治疗方向优化路线见 [`optimization-roadmap.md`](./optimization-roadmap.md)。
- Case content (the teen-truancy scenario JSON) → `case-content` task (W2).
- School templates (结构派 / Bowen / 萨提亚) → `school-templates` task (W2).
- Persistence layer implementation (SQLAlchemy / Alembic / pgvector schema) → `persistence` task (W2).
- Real LLM integration & streaming → `integration-verify` task (W2).
- UI pages beyond a "service is up" landing → `web-ui` task (W2+).
- Auth, multi-user, payments → v0.2+ (out of MVP).

---

## 10. Verification checklist for this task

The task is "done" only when all of these are true:

- [x] All directories from §2.2 exist.
- [x] `docs/architecture.md` (this file) is committed.
- [x] `packages/memory/schema.py` exports the models in §3 without import errors.
- [x] `packages/agents/base.py` defines `BaseAgent` Protocol + `SessionContext` + `Hint`.
- [x] `packages/agents/{doctor,psychiatrist,observer,father,mother,patient}.py` exist as stubs conforming to `BaseAgent`.
- [x] `packages/agents/orchestrator.py` compiles a `StateGraph` with the topology in §5.
- [x] `apps/api/app/main.py` boots a FastAPI app and exposes `GET /healthz`.
- [x] `apps/web/app/page.tsx` (or `.jsx`) shows a "HealTeam MVP — service online" placeholder.
- [x] `docker-compose.yml` defines `api`, `web`, `postgres` services with healthchecks.
- [x] `.env.example` documents every env var.
- [x] `scripts/smoke.py` exits 0 with no LLM call and no DB connection.
- [x] `README.md` documents the full bring-up flow.

