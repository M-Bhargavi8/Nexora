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
>
> ✅ **Closed out (2026-08-21):** Docker Desktop and WSL 2 installed and
> running; the stack comes up with `docker compose up -d`. See Phase 1 below
> for the live-database verification this unblocked.

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

**Verified against a live database:** ✅ **2026-08-22.** When Phase 1 was
built, Docker was not installed, so the "database reachable" path was covered
only by tests using a fake. That gap is now closed — every item below was
executed against the real database, not asserted:

- `docker compose up -d` → `nexora-postgres` **healthy**, bound to
  `127.0.0.1:5432`; container logs clean, `pg_isready` accepting connections
- PostgreSQL **17.11**; the init script ran and `pgvector` **0.8.6** was tested
  *functionally*, not just for presence:
  `'[1,2,3]'::vector <-> '[4,5,6]'::vector` → `5.196…` (= √27)
- **FastAPI connects** through its own code path — `check_database_connection()`
  returned `True` over `asyncpg`, `current_database: nexora`
- **`GET /api/v1/health/ready` → 200** `{"status":"ready","checks":[{"name":
  "database","healthy":true}]}` (the success path, previously unproven)
- **Alembic connects** (`Context impl PostgresqlImpl`). `upgrade head` →
  `alembic check` ("No new upgrade operations detected") → `downgrade base`
  were all exercised against a **throwaway** database, so the `nexora`
  database is untouched and still at base
- **Frontend** status page flipped from *Database: Unavailable* to
  **Database: Connected**; no console errors
- **45 committed backend tests passing**

Root cause of the delay, for the record: Docker Desktop crashed at startup on
orphaned AF_UNIX socket reparse points left by an unclean shutdown
(`ERROR_CANT_ACCESS_FILE`), which presents as a *hanging* `docker version`
rather than an error. Fixed by renaming the parent directories so Docker
recreates them.

## Phase 2 — Authentication ✅
Signup, login, logout, password hashing, protected routes, session/token
handling, per-user data isolation. Tested thoroughly.

**Delivered and verified (2026-08-22):**
- Argon2id password hashing, with transparent rehash on login
- Session tokens: 256 bits of randomness, only the SHA-256 hash stored, so a
  database dump contains no usable credential
- httpOnly + SameSite=lax session cookie; the token is never in JavaScript,
  localStorage or sessionStorage
- Uniform 401 for every failure mode, and a dummy Argon2 verification when the
  email is unknown, so response time does not reveal which emails are registered
- CSRF origin checking on state-changing methods
- Two-dimensional login rate limiting (per-IP and per-account), counting only
  failures, resetting the account counter on a genuine sign-in
- React Router with a `RequireAuth` guard, session restoration on load, and
  redirects in both directions
- **153 backend tests, 26 frontend tests**, all passing

**Known limitations, stated deliberately:**
- The rate limiter is in-process. Multiple workers each keep their own counters;
  a distributed limiter needs Redis, which arrives in a later phase.
- Signup returns 409 for a duplicate email, which confirms that an address is
  registered. Closing that needs email delivery, which does not exist yet.
- Behind a reverse proxy every request appears to come from the proxy, so
  production must read `X-Forwarded-For` from a *trusted* proxy only.
- No password reset or change flow — out of scope for this phase.

## Phase 3 — Basic AI Chat ✅
User → React → FastAPI → AI Gateway → Model Provider → streaming response →
React. New chat, streaming, save messages, history, markdown, code blocks,
stop generation, regenerate.

**Delivered and verified (2026-08-23):**
- Provider-independent AI gateway; an offline `echo` provider so the whole
  streaming path is testable without calling — or paying for — a real model
- `POST /chat` streams `start` / `delta` / `error` / `done` over SSE; the reply
  is persisted as it completes, so a dropped connection loses nothing
- `POST /chat/regenerate` discards the last answer and streams a new one to the
  **stored** question, so the client cannot substitute a different question and
  have it attributed to the original turn
- Stop generation via `AbortController`; partial text is kept, since the server
  already saved it and hiding it would show less than exists
- Markdown and syntax-highlighted code blocks, with copy-to-clipboard
- Conversation history in the sidebar, scoped to the signed-in user
- Per-user rate limiting on both send and regenerate — regenerating costs a
  full model call and is the easiest thing in the product to sit and hammer
- **241 backend tests, 90 frontend tests**, all passing

**Verified against the running stack, not just tests:** signed up through the
UI, sent a message, watched it stream, confirmed markdown/table/code rendering,
pressed *Regenerate reply*, confirmed `POST /chat/regenerate → 200`, and queried
PostgreSQL directly to confirm the conversation still held exactly one question
and one answer at contiguous positions — the old reply was replaced, not
duplicated. Test account deleted afterwards.

**Known limitations, stated deliberately:**
- The only provider wired up is `echo`. Nothing here proves a real model
  provider works; that needs credentials and an actual call.
- Regenerating discards the previous answer. There is no way to compare
  alternative replies, which would need a notion of which reply later turns
  build on — not needed in this phase.
- Editing an earlier message is not implemented (Phase 4).

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
