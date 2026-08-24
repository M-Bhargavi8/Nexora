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

## Phase 4 — Conversation Management ✅
Rename, delete, search, history pagination, message editing.

**Delivered and verified (2026-08-23):**
- `PATCH /chat/conversations/{id}` renames; titles are whitespace-collapsed and
  a blank one is rejected, so a title can never render as an invisible row
- Renaming deliberately does **not** bump `updated_at`. The sidebar is ordered
  by when a conversation was last *used*, and retitling an old thread should
  not send it to the top. This meant dropping `onupdate=utcnow` from the
  column — a Python-side default, so no migration was required
- `DELETE /chat/conversations/{id}` → 204, cascading to every message. A real
  delete, not a soft one
- `GET /chat/conversations?q=&limit=&offset=` searches **titles and message
  text** — title-only search would miss the thing you remember saying, since
  titles are generated from the first message
- `LIKE` metacharacters in the search term are escaped, so searching for `100%`
  or `get_user` matches those strings instead of everything
- Pagination returns `has_more` by fetching one row beyond the page, avoiding a
  `COUNT(*)` on every keystroke. `limit` is capped at 100 by the route
- `POST /chat/edit` rewrites one of your own questions, discards every turn
  after it, and streams a new reply. Editing the first message re-derives the
  conversation title
- **Only user turns are editable.** Rewriting an assistant reply would let a
  caller put words in the model's mouth, and those words return as context on
  every later turn — self-inflicted prompt injection with a store behind it
- Sending, regenerating and editing share one rate-limit counter, so a caller
  cannot spend triple the budget by rotating between endpoints
- Search is debounced (250ms) and guards against out-of-order responses, so a
  slow reply for an abandoned term cannot overwrite current results
- **286 backend tests, 128 frontend tests**, all passing

**Verified against the running stack, not just tests:** signed in through the
UI and exercised each feature against real PostgreSQL —

- searched `pgvector` and matched a conversation titled *Postgres tuning notes*,
  proving the match came from **message text**, not the title
- searched a literal `%` and got **no results**; unescaped it would have matched
  all three conversations — the wildcard-escaping proven, not asserted
- renamed with `   Embeddings     research   ` → stored as `Embeddings research`,
  and the row **stayed in third place** with `updated_at` unchanged
- edited the first question of a 4-message thread: the database went to exactly
  two rows at contiguous positions and the title was re-derived
- confirmed exactly one Edit control per user turn and none on replies
- deleted a conversation: one click asked for confirmation, the second removed
  it, the open transcript cleared, and **zero orphaned message rows** remained
- paged with `limit=1`: page one reported `has_more: true`, page two returned
  the next row with `has_more: false`; `limit=101` and `offset=-1` both 422

Test account deleted afterwards. `alembic check` on a throwaway database
reported "No new upgrade operations detected", confirming no migration is due.

**Known limitations, stated deliberately:**
- Search is `ILIKE '%term%'`, which cannot use an index — a sequential scan,
  fine at one user's scale and not beyond it. Ranked full-text search
  (`tsvector` + GIN) is the upgrade; meaning-based search arrives in Phase 6.
- Messages within a conversation are not paginated; a very long thread still
  loads whole. Paginating the UI while `load_history` sends the entire thread
  to the model would be inconsistent — the real fix is history trimming in
  Phase 9.
- Editing replaces rather than branches, so alternative versions of a question
  cannot be compared. Same position `regenerate` took.
- Still only the offline `echo` provider. Nothing here proves a real model
  provider works.

## Phase 5 — File Upload ✅
TXT, PDF, DOCX first; then CSV, XLSX, images. Validation, storage, size
limits, MIME checks.

**Delivered and verified (2026-08-24):**
- `POST /files` accepts multipart uploads; `GET /files` lists them (paginated),
  `GET /files/{id}` returns metadata plus extracted text, `/download` serves
  the original bytes, `DELETE` removes row *and* bytes
- Text extraction for **TXT, MD, CSV, PDF, DOCX, XLSX**. DOCX reads tables as
  well as paragraphs — in real documents the tables carry the facts. XLSX reads
  cells across every sheet with formulas resolved to values
- Images (PNG/JPEG/GIF/WEBP) are validated and stored but not read, and say so
  rather than returning a silent empty result. Reading them needs a vision
  model, which is a later phase
- **The user's filename never becomes a path.** It is stored as a display label
  only; the path is built from a `storage_key` this application generates (a
  bare UUID hex, no extension). That makes traversal structurally impossible
  rather than something a validator must keep catching
- **Content decides the type, not the extension.** Magic bytes are checked
  first, then the extension must agree — in *both* directions
- Size limit is enforced **while streaming**, never from `Content-Length`,
  which a client can understate or omit
- Extraction is bounded by characters and PDF pages (zip bombs, CPU exhaustion)
  and runs in a worker thread so a large parse cannot stall the event loop or
  the chat streams
- Downloads are served `application/octet-stream` + `Content-Disposition:
  attachment` + `nosniff`, so uploaded content cannot execute on our origin
- A file that is accepted but unparseable is **kept** and marked, not deleted;
  error text is written for the user and never copied from an exception
- No `python-magic`: it needs native libmagic. The accepted set is small and
  fixed, so signatures are checked directly — one dependency fewer
- **384 backend tests, 147 frontend tests**, all passing

**Verified against the running stack with real files**, not only in tests:
uploaded a genuine 2-page PDF, DOCX with a table, multi-sheet XLSX, CSV, UTF-8
TXT and PNG —

- every format extracted correctly; an em dash and an emoji survived chunked
  reads intact; the DOCX table came through as `EMEA | 421000`
- HTML wearing a `.png` name → **400**, rejected on its contents
- a 25MB upload → **400**, and nothing written to disk
- filename `../../../etc/passwd.txt` → stored as label `passwd.txt`; on disk
  every file is a flat 32-hex name directly under the upload root
- download headers confirmed: `octet-stream`, `attachment`, `nosniff`
- a second account got **404** on read, download *and* delete of another user's
  file, and the bytes stayed intact
- owner delete → 204, row gone, **bytes gone from disk**
- **13 rows, 13 files on disk** — rejected uploads left no orphans
- in the UI: `.exe` blocked client-side, HTML-as-PNG blocked server-side, text
  preview rendered as plain text, upload progress and status badges correct

Migration `0003_files` upgrades, passes `alembic check`, and downgrades cleanly
against a throwaway database. Test accounts and files removed afterwards.

**Known limitations, stated deliberately:**
- **Deleting a user orphans their uploaded bytes.** The FK cascade removes the
  rows, but disk has no foreign keys, and once the rows are gone nothing knows
  which files to unlink. There is no account-deletion feature yet; when one is
  built it must unlink storage *first*. Recorded on the model too.
- Extraction happens inside the request, so the user waits for it and a slow
  parse holds a connection. The caps make this acceptable; a job queue is the
  real answer once uploads get large or frequent.
- Scanned PDFs yield nothing — there is no OCR.
- Files are not yet usable from chat. Attaching them is Phase 6 (RAG), which
  chunks and embeds `extracted_text` rather than pasting whole documents in.
- Storage is a local directory, so this does not survive a container rebuild
  and does not work across multiple machines. The interface is narrow (write,
  open, delete by key) precisely so object storage can replace it later.
- No virus scanning. NEXORA never executes an upload, and downloads are forced
  as attachments, but a file that is malicious *to the person who downloads it*
  is not something this phase detects.

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
