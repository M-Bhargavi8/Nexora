# NEXORA — Architecture (Phase 0)

This document describes the **target** architecture for NEXORA. It is a plan,
not a description of code that exists yet. Every phase in `ROADMAP.md` builds
one piece of this picture, in order.

---

## 1. Environment Check

Before recommending a stack, we confirmed what the development machine
actually has installed, so the plan is grounded in reality rather than
assumption. Every row below was verified by running the tool, not inferred.

**Machine:** Windows 11 Pro (build 26200) · 15.7 GB RAM · 132.9 GB free on `C:`
**Verified:** 2026-08-16

### 1.1 Present

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11.9 | Fully supports FastAPI, SQLAlchemy 2.x, Pydantic v2. Good choice — every major AI/ML library ships 3.11 wheels. |
| Node.js | v24.13.0 | LTS-class. Vite requires ≥ 20.19, so we are comfortably ahead. |
| npm | 11.6.2 | Our package manager. No need for pnpm/yarn. |
| pip | 26.1.2 | Our Python package manager, used inside a venv. |
| git | 2.52.0.windows.1 | Installed. Repository initialized in Phase 0. |
| VS Code | present | Editor. |
| winget | present | Windows package manager — how we install what's missing. |

### 1.2 Missing (as found at Phase 0 — since resolved, see below)

| Tool | Status | Impact |
|---|---|---|
| **PostgreSQL** | Not installed — no `psql`, no server, no service, nothing listening on 5432 | **Blocks Phase 1.** Must be resolved before the database layer is built. See §1.3. |
| **Docker** | Not installed — not on `PATH`, no install directory, no service | Not required until Phase 15 (production), *unless* we choose it as the way to run Postgres. See §1.3. |
| Redis | Not installed, nothing on 6379 | No impact. Correctly deferred — no phase before ~7 needs caching or background jobs. |
| pnpm / yarn / uv / poetry | Not installed | No impact. npm + pip + venv cover everything we need. |

Ports 5432, 6379, 8000 (planned API) and 5173 (planned Vite dev server) are all
free — no conflicts to work around.

> ### ✅ Gap closed (2026-08-21/22)
>
> The table above is the **Phase 0 snapshot** and is kept as the record of what
> the decision in §1.3 was made against. Both blocking gaps are now resolved:
>
> | Tool | Now | Verified by |
> |---|---|---|
> | Docker | **29.7.2** + Compose **v5.3.1** | `docker version` against a live daemon |
> | PostgreSQL | **17.11** in `nexora-postgres`, healthy on `127.0.0.1:5432` | `docker compose ps`, `pg_isready` |
> | pgvector | **0.8.6** | `'[1,2,3]'::vector <-> '[4,5,6]'::vector` → `5.196…` (= √27) |
>
> Redis remains deliberately absent — no phase before ~7 needs it.
>
> Still missing, and tracked rather than assumed away: **no frontend test
> runner** (`frontend/package.json` has no `test` script). Vitest + React
> Testing Library are needed to satisfy the testing standard, ideally before
> Phase 3 puts real logic in the UI.

### 1.3 Open decision — how we run PostgreSQL

This is the one blocking question for Phase 1, and it is a real trade-off, not
a formality. NEXORA needs PostgreSQL **with the `pgvector` extension** (the
extension is what makes RAG possible in Phase 6 — it lets Postgres store and
search embedding vectors).

**Option A — Docker Desktop, run Postgres in a container**
- Pros: the official `pgvector/pgvector` image ships Postgres *and* pgvector
  pre-built, so there is nothing to compile; identical setup on any machine;
  Redis later is one more line; Phase 15 needs Docker anyway.
- Cons: multi-GB install, requires WSL2, uses ~2 GB RAM while running, and
  adds one more concept to learn now.

**Option B — Native PostgreSQL for Windows, add pgvector manually**
- Pros: lighter, starts with Windows, no new virtualization concepts.
- Cons: `pgvector` has no official Windows installer — it normally has to be
  compiled with MSVC build tools. This is a genuine and unpleasant hurdle for
  a beginner, and it hits us in Phase 6 when the project is much larger.

**Option C — SQLite now, Postgres later**
- Pros: zero installation, Phase 1 starts immediately.
- Cons: SQLite cannot do vector search, so Phase 6 forces a database
  migration mid-project. Trading a one-hour setup now for a multi-day
  rewrite later.

**Recommendation: Option A (Docker Desktop).** The pgvector-compiling problem
in Option B is the deciding factor — it is the exact kind of obstacle that
stalls a project two months in. Docker also means the database we develop
against is the same one we deploy, which is a real engineering answer to give
in an interview.

> ### ✅ Decision (2026-08-16): Option A — Docker Desktop
>
> PostgreSQL and Redis run as containers in local development, using the
> official `pgvector/pgvector` image so the vector extension needs no
> compilation. Docker is installed by the developer (it requires
> administrator rights, enables WSL2, and may need a reboot — so it is not
> automated).
>
> **Consequences accepted:**
> - Docker Desktop must be running for the backend's database layer to work.
>   Phase 1 will fail fast with a clear error message if it is not, rather
>   than hanging on a connection timeout.
> - ~2 GB RAM overhead while running (we have 15.7 GB — acceptable).
> - Database credentials and ports live in `.env`, never in committed files.
> - Phase 15 (production) already has Docker as a prerequisite, so this is
>   groundwork rather than a detour.
>
> **Rejected:** native Postgres (no official Windows pgvector build), and
> SQLite (no vector search — would force a migration at Phase 6).

### 1.4 Deferred, by design

- **`.env` / secrets handling** — created in Phase 1 with the config system.
- **AI provider credentials** — requested when Phase 3 begins, not before.
  No key is needed to build Phases 1–2, and asking early would mean an unused
  secret sitting on disk.

**Conclusion:** the machine supports the planned stack. Python, Node, and git
are ready. The only genuine gap is PostgreSQL, and it must be closed before
Phase 1's database work — not assumed away.

---

## 2. Target System Architecture

```text
                         NEXORA
                            |
                            v
                    AI ORCHESTRATOR
                            |
          +-----------------+-----------------+
          |                 |                 |
          v                 v                 v
      MODEL GATEWAY       TOOLS            MEMORY
          |                 |                 |
     +----+----+       +----+----+       +----+----+
     |    |    |       |    |    |       |         |
     v    v    v       v    v    v       v         v
   LLM  Vision Code   Search Python Files Short   Long
                                          Term    Term
          |
          v
        RAG
          |
          v
     PostgreSQL
       + pgvector
          |
          v
        AGENTS
          |
          v
       RESPONSE
```

This is the **end state**, roughly Phase 9–13. Early phases implement only a
thin vertical slice of it (see `ROADMAP.md`).

### 2.1 High-level component responsibilities

- **AI Orchestrator** — the single entry point that decides, per request,
  which combination of model, tools, retrieved context, and memory to use.
  It is the only part of the backend allowed to talk to the Model Gateway
  directly with business logic attached.
- **Model Gateway** — a provider-independent abstraction. Nothing else in
  the codebase imports a vendor SDK directly.
- **Tools** — narrow, permissioned capabilities (web search, Python
  execution, file analysis, calculator) that the orchestrator/agents can
  invoke, each with its own schema, timeout, and audit log entry.
- **Memory** — short-term (current conversation), long-term (user
  preferences/facts), and project memory, always user-controlled.
- **RAG** — the pipeline that turns uploaded documents into searchable,
  embedded chunks in `pgvector`, and turns a question into a grounded,
  cited answer.
- **Agents** — multi-step planners that can call tools and observe results
  in a loop until a task is complete, with a visible (not hidden)
  activity summary in the UI.

---

## 3. AI Model Abstraction

We will not scatter provider SDK calls throughout the application. Every
model call goes through one interface:

```text
ModelProvider
    |
    +-- generate()   # single-shot completion
    +-- stream()     # streaming completion (used by chat)
    +-- embed()      # text -> vector, for RAG
    +-- vision()     # image understanding
    +-- tool_call()  # structured tool/function calling
```

```text
Frontend
    |
    v
FastAPI
    |
    v
AI Gateway            <-- implements ModelProvider interface
    |
    v
Model Provider (configurable via env vars: Provider A / B / C / local model)
```

**Why this matters:** if a model provider changes pricing, gets deprecated,
or a better one appears, we swap one adapter behind the interface. No route
handler, no frontend component, and no prompt-construction logic needs to
change. This is the single most important architectural decision in the
whole project, so it is called out even though it isn't implemented until
Phase 3.

---

## 4. Data Flow (Chat, once RAG/tools exist)

```text
Upload
    ↓
Validation
    ↓
File storage
    ↓
Parsing
    ↓
Text/table extraction
    ↓
Chunking
    ↓
Embedding
    ↓
pgvector
    ↓
Retrieval
    ↓
LLM
```

Each arrow is a real, separately testable step — not one giant function.
Chunking, embedding, and retrieval are each swappable independently (e.g.
change chunk size, change embedding model) without touching the others.

## 5. Agent Flow (Phase 10+)

```text
User Request
      ↓
Understand
      ↓
Plan
      ↓
Select tools
      ↓
Execute
      ↓
Observe
      ↓
Evaluate
      ↓
Continue or finish
      ↓
Final response
```

The UI shows a concise activity summary (e.g. "Analyzing request → Searching
sources → Reading uploaded file → Verifying result"), never the model's raw
internal reasoning.

## 6. Model Router (Phase 8+)

```text
User Request
      ↓
Intent Detection
      ↓
Model Router
      |
      +-- General
      +-- Coding
      +-- Vision
      +-- Reasoning
      +-- Long Context
      +-- Data Analysis
```

The router weighs capability, context size, latency, and cost — it does not
default to the most expensive model for every request. This is a cost and
performance decision, not just a technical one.

---

## 7. How Frontend, Backend, Database, and AI Providers Communicate

```text
Browser (React + TS)
      | HTTPS (REST for CRUD, SSE/streaming for chat tokens)
      v
FastAPI (Python)
      | SQLAlchemy (async) — parameterized queries only
      v
PostgreSQL + pgvector
      |
      | (separately) FastAPI -> AI Gateway -> Model Provider over HTTPS
      v
Redis (cache / background job queue) — introduced only when a phase needs it
```

- The frontend never talks to the database or the AI provider directly —
  everything goes through FastAPI. This keeps secrets server-side and lets
  us enforce auth/rate limits/validation in one place.
- Chat responses stream token-by-token from the model provider, through
  FastAPI, to the browser (Server-Sent Events or a comparable streaming
  transport — the exact mechanism is decided and explained in Phase 3).

---

## 8. Database Design (Introduced Incrementally)

Eventual entities (created only as each phase needs them — not upfront):

```text
users, sessions, conversations, messages, files, file_chunks, embeddings,
projects, project_members, memories, tool_calls, agent_tasks, model_usage,
api_usage, settings, audit_logs
```

All schema changes go through Alembic migrations. No manual production
schema edits.

---

## 9. Security Model (Designed In From Phase 1)

- Secure authentication, password hashing, and session/token handling
  (Phase 2)
- Authorization and per-user data isolation on every query
- Input validation on every API boundary (Pydantic models)
- File validation: type, MIME sniffing, and size limits before anything is
  parsed
- Rate limiting on public endpoints
- Secrets only via environment variables / a secret manager in production —
  never hard-coded, never committed
- SQL injection protection via the ORM's parameterized queries (no raw
  string-built SQL)
- XSS protection on anything rendered from user or model content
- CSRF protection where cookies are used for auth
- Prompt-injection–aware tool design: tools have explicit permission rules
  and cannot be silently escalated by model output
- Any code execution (Phase 12) runs in an isolated sandbox — never inside
  the FastAPI process — with CPU/memory/timeout limits and no network access
  by default
- Structured audit logging for sensitive actions, without logging sensitive
  payloads unnecessarily

## 10. Environment Variables We Will Eventually Need

These are **not** created yet — this is a forward-looking checklist so we
know what Phase 1's configuration system must support:

```text
# App
APP_ENV=development|production
APP_SECRET_KEY=

# Database
DATABASE_URL=postgresql+asyncpg://...

# Redis (from the phase that first needs it)
REDIS_URL=

# Auth
JWT_SECRET=
JWT_EXPIRY_MINUTES=

# AI Providers (names illustrative — decided per-provider when Phase 3 starts)
AI_PROVIDER=
AI_API_KEY=
AI_MODEL_DEFAULT=

# File storage
FILE_STORAGE_PATH=
MAX_UPLOAD_SIZE_MB=

# Web search tool (Phase 7)
SEARCH_API_KEY=

# CORS
FRONTEND_ORIGIN=
```

`.env` is used in development and is git-ignored. Production uses proper
environment-level secret management (the specific mechanism is decided in
Phase 15).

## 11. What I Should Understand From This Document (Interview Framing)

- Why a **provider-independent Model Gateway** matters (vendor risk, testing,
  cost control) — this is the architectural decision most worth being able
  to explain clearly.
- Why the frontend **never** talks directly to the database or AI provider.
- Why RAG is a pipeline of separately-testable steps rather than one
  function.
- Why code execution must be isolated from the main API process.
- Why memory is explicitly user-controlled rather than an invisible feature.
