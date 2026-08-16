# NEXORA — Roadmap

Each phase is implemented only after the previous one is approved. Every
phase follows the same loop: explain → implement → test → verify → explain
what changed → give run commands → wait for approval.

Legend: ✅ done · 🔵 in progress · ⚪ not started

---

## Phase 0 — Architecture ✅ (this phase)
Inspect environment, define stack, write `README.md`, `ARCHITECTURE.md`,
`ROADMAP.md`, `.gitignore`, `.gitattributes`. Initialize the git repository.
No app code.

**Verified outcome:** environment inspected by actually running each tool
(see `ARCHITECTURE.md` §1). Python 3.11.9, Node v24.13.0, npm 11.6.2 and
git 2.52.0 confirmed present. **PostgreSQL and Docker confirmed absent.**

> ✅ **Resolved (2026-08-16):** PostgreSQL + pgvector will run in Docker,
> using the official `pgvector/pgvector` image (`ARCHITECTURE.md` §1.3).
> Docker Desktop is installed manually by the developer, since it needs
> administrator rights and possibly a reboot.

## Phase 1 — Foundation ✅
React + TypeScript + Vite + Tailwind, FastAPI, PostgreSQL. Project structure,
health endpoint, DB connection, config system, env handling, logging, basic
frontend, and confirmed frontend↔backend communication.

**Delivered and verified:**
- FastAPI app factory with typed settings (fails fast on missing/short secrets)
- Structured logging with per-request correlation ids (console + JSON formats)
- Liveness (`/api/v1/health`) and readiness (`/api/v1/health/ready`) endpoints
- Async SQLAlchemy engine, session dependency, and a **bounded** readiness probe
- Alembic configured for async migrations, credentials injected at runtime
- React + TypeScript + Tailwind v4 status page with a typed API client
- `docker-compose.yml` running `pgvector/pgvector:pg17`, bound to loopback only
- 45 backend tests passing

**Verified against a live database:** ❌ not yet. Docker was not installed on
the dev machine when Phase 1 was built, so the "database reachable" path is
covered by tests using a fake, not by a real connection. The failure path
(*database unreachable → 503*) was verified for real. This must be closed out
before Phase 2 depends on the database.

## Phase 2 — Authentication ⚪
Signup, login, logout, password hashing, protected routes, session/token
handling, per-user data isolation. Tested thoroughly.

## Phase 3 — Basic AI Chat ⚪
User → React → FastAPI → AI Gateway → Model Provider → streaming response →
React. New chat, streaming, save messages, history, markdown, code blocks,
stop generation, regenerate.

## Phase 4 — Conversation Management ⚪
Rename, delete, search, history pagination, message editing.

## Phase 5 — File Upload ⚪
TXT, PDF, DOCX first; then CSV, XLSX, images. Validation, storage, size
limits, MIME checks.

## Phase 6 — RAG ⚪
Chunking, embeddings, pgvector, retrieval, contextual answering, source
references.

## Phase 7 — Tools ⚪
Calculator, web search, file analysis, Python/data analysis, code analysis —
each with schema, validation, permission rules, timeout, error handling,
logging.

## Phase 8 — Model Router ⚪
Intelligent model selection by capability, context size, latency, cost.

## Phase 9 — Memory ⚪
Conversation memory, project memory, user preferences, full user memory
controls (view/edit/delete/disable).

## Phase 10 — Research Agent ⚪
Planning, search, source collection, synthesis, citation, verification.

## Phase 11 — Coding Agent ⚪
Requirement understanding, project planning, file generation, controlled
execution, testing, error detection and correction.

## Phase 12 — Data Analyst ⚪
CSV/XLSX analysis, sandboxed Python execution, charts, statistics,
natural-language insights.

## Phase 13 — Multi-Agent Architecture ⚪
Manager / Research / Coding / Data / Document agents — only once the
single-agent architecture is stable.

## Phase 14 — Evaluation ⚪
Evaluation datasets and metrics for answer quality, hallucination, tool
selection, RAG retrieval, citation accuracy, coding accuracy, data-analysis
accuracy, agent completion, and safety.

## Phase 15 — Production ⚪
Docker, production config, reverse proxy, monitoring, rate limits, security
hardening, backups, CI/CD, deployment docs.

---

## Working Agreement

- No phase starts until the previous one is explained, implemented, tested,
  verified, and explicitly approved.
- Every new dependency is justified before it's installed.
- Every commit is scoped to one logical change (see commit style below).
- Nothing is claimed to work unless it has actually been run and tested.

### Commit Style

```text
feat: initialize nexora frontend
feat: initialize fastapi backend
feat: add database configuration
feat: add authentication
feat: add streaming chat
feat: add conversation persistence
docs: ...
fix: ...
test: ...
```
