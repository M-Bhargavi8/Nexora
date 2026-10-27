# NEXORA — Project Status

The single place that says what is done, what is not, and what was actually
verified. `ROADMAP.md` keeps the per-phase evidence; this file is the summary
and the work queue. Statuses: **NOT STARTED · IN PROGRESS · BLOCKED · COMPLETE**.

Last updated: 2026-10-26.

---

## 1. Audit (2026-10-26, before any change)

### Repository state
- Branch `main`, HEAD `83629ce` (*docs: rewrite README to match phases 0-6*),
  27 commits, working tree clean, remote `github.com/M-Bhargavi8/Nexora`.
- Live dev database (`nexora-postgres`, healthy) is at Alembic head
  `0004_file_chunks` and holds **0 files and 0 chunks**, so no stored vectors
  are at risk from the embedding work below.
- `backend/.env` sets only app, secret, database, CORS and logging keys. **No
  `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` is configured**, so no real model
  provider can be exercised live from this machine. (Values were not read; only
  key names were listed.)

### Tests (run, not assumed)
| Suite | Command | Result |
|---|---|---|
| Backend | `cd backend && python -m pytest` | **492 passed**, 0 skipped (PostgreSQL tests ran against `nexora_test`), 4m47s |
| Frontend | `cd frontend && npm test` | **156 passed** across 11 files, ~60s |

### Verified completed features
Phases 0–6 plus the Gemini chat provider, as recorded in `ROADMAP.md`: config
and logging, health/readiness, Argon2id auth with hashed session tokens, CSRF
origin checks, IP and account login limits, SSE chat with stop/regenerate/edit,
conversation rename/delete/search/pagination, content-checked uploads with
extraction for TXT/MD/CSV/PDF/DOCX/XLSX, chunking, pgvector retrieval scoped by
user, and citations built from retrieval results.

### Incomplete or weak features
1. **Retrieval is lexical.** Only the hashing embedding provider exists.
2. **Stored vectors carry no model identity.** Changing the embedding provider
   would compare vectors from two models as if they were equivalent.
3. **No re-index or backfill command.** Files uploaded before Phase 6, or whose
   indexing failed, can never become searchable.
4. Extraction and indexing run inside the upload request; there is no job
   queue, no retry, and no visible "indexing failed" state.
5. No password reset, password change, or account deletion.
6. Long conversations load whole; message search is an unindexed `ILIKE`.
7. Phases 7–15 (tools, router, memory, agents, evaluation, production) are not
   started.

### Security and reliability risks
- Signup returns 409 for an existing email (account enumeration).
- The rate limiter is in-process; it does not hold across workers.
- Deleting a user would orphan their uploaded bytes on disk.
- Indexing deletes old chunks *before* calling the embedding provider, holding
  a write transaction open across a network call.
- Filtered IVFFlat search (by `user_id`) can return fewer results than exist
  once the table is large, because the default probe count scans one list and
  filters afterwards. Not observable at current scale; needs pgvector's
  iterative scan or HNSW before real growth.
- No embedding call timeout.

### Missing tests
- No test proves vectors from different models are kept apart (there was only
  one model).
- No backfill or re-index recovery tests.
- No retrieval-quality evaluation set (relevant / irrelevant / adversarial).
- No account lifecycle tests (reset, change, delete) because the features do
  not exist.

### Database migration risks
- The vector column is fixed at `vector(512)`. Any embedding model must emit
  512 dimensions, or a migration plus a full re-embed is required.
- Migrations are linear (`0001`→`0004`) and each was verified up/down on a
  throwaway database; no drift reported by `alembic check`.

### Dependency and configuration gaps
- No backend linter, formatter or type checker is installed or configured
  (no ruff/mypy). Frontend has `oxlint` and `tsc -b`.
- No CI configuration (`.github/` absent).
- No backend or frontend Dockerfile; Compose runs only PostgreSQL.
- No Redis, no object storage, no worker process.

### Prioritized plan
1. **Gap A — semantic embeddings** (this increment): model identity on every
   chunk, a Gemini embedding adapter, model-aware retrieval, and a resumable
   re-index/backfill command.
2. **Gap B — background indexing**: job table, worker, retries, status in UI.
3. **Gap C — account security**: generic signup response, password change and
   reset, account deletion with file cleanup.
4. **Gap D — performance**: message pagination, search index, filtered vector
   search, retrieval evaluation set.
5. Phases 7 → 15 in roadmap order.

---

## 2. Phase and gap status

| Item | Status | Notes |
|---|---|---|
| Phases 0–6 | COMPLETE | Evidence in `ROADMAP.md` |
| Gemini chat provider | COMPLETE (mock-tested) | Live call not verified here: no key configured |
| Gap A — semantic embeddings | IN PROGRESS | Code, migration and tests done; live Gemini call and threshold re-tune need an API key. See §3 |
| Gap B — background indexing | NOT STARTED | |
| Gap C — account security | NOT STARTED | |
| Gap D — retrieval/conversation performance | NOT STARTED | |
| Phase 7 — Tools | NOT STARTED | |
| Phase 8 — Model router | NOT STARTED | |
| Phase 9 — Memory | NOT STARTED | |
| Phase 10 — Research agent | NOT STARTED | |
| Phase 11 — Coding agent | NOT STARTED | |
| Phase 12 — Data analyst | NOT STARTED | |
| Phase 13 — Multi-agent | NOT STARTED | |
| Phase 14 — Evaluation | NOT STARTED | |
| Phase 15 — Production | NOT STARTED | |

---

## 3. Increment log

### Gap A — semantic embeddings

**Status: IN PROGRESS.** The code, migration and tests are done and pass. It
stays IN PROGRESS until a live Gemini embedding call is verified and the
similarity floor is re-tuned against real scores. Both need a `GEMINI_API_KEY`
that this machine does not have.

**Implemented**
- `EmbeddingProvider` now has a `model` and an `identity`
  (`provider:model:dimensions`), and `embed()` takes `purpose="document"|"query"`.
  The default keeps the old call shape working.
- `GeminiEmbeddingProvider` (`EMBEDDING_PROVIDER=gemini`). It uses separate
  task types for documents and queries, asks for exactly the column width
  (512), checks the width and count of what comes back, L2-normalises,
  batches 50 texts per request, enforces a per-request timeout, and keeps the
  provider's error text away from callers.
- Every chunk stores `embedding_model`. Search and `count_chunks` compare only
  against the active model, so vectors from different models are never
  compared.
- Indexing now embeds *before* deleting the old chunks, and swaps them in one
  transaction. A provider outage leaves the old chunks intact and holds no
  write transaction across a network call.
- `python -m scripts.reindex [--dry-run] [--limit N] [--user-id UUID]` finds
  READY files with text that have no chunks, or chunks from another model, and
  re-embeds them one file per transaction. Pages are keyset-paginated by id.
  The command is resumable and idempotent. Failures are skipped and reported,
  the exit code is 1 if any file failed, and it never prints document text or
  filenames.
- Settings: `EMBEDDING_PROVIDER=gemini` requires `GEMINI_API_KEY`. Production
  now refuses `hashing`, as the old config comment promised once a second
  provider existed.

**Files changed**: `app/ai/base.py`, `app/ai/gateway.py`,
`app/ai/providers/gemini_provider.py`, `app/ai/providers/hashing_embeddings.py`,
`app/core/config.py`, `app/models/chunk.py`, `app/services/retrieval.py`,
`.env.example`, `tests/test_config.py` (the production baseline helper now
uses gemini embeddings).
**New**: `alembic/versions/0005_add_chunk_embedding_model.py`,
`scripts/reindex.py`, `tests/test_embeddings.py` (26 tests, fake SDK client),
`tests/test_reindex.py` (13 tests, real PostgreSQL).

**Migration `0005_chunk_embedding_model`**: adds `file_chunks.embedding_model`
`varchar(200) NOT NULL` and the index `(user_id, embedding_model)`. Existing
rows are back-filled with `hashing:hashing-v1:512`. That value is a fact, not a
guess: hashing was the only provider that existed before this revision. The
server default is then dropped, so new rows must name their model.
Verified on a throwaway database (`nexora_migcheck_0005`, dropped afterwards):
- a chunk seeded at 0004 got `hashing:hashing-v1:512` after the upgrade;
- `alembic check` reported no drift;
- an insert without a model is rejected by NOT NULL;
- the downgrade kept the row and its 512-dimension vector;
- re-upgrading worked.
The dev database `nexora` was **not** touched and is still at `0004`.

**Tests**: see §4.

**Known limitations**
- **Not verified live.** No API key is configured here, so the Gemini adapter
  is tested only against a fake client shaped like the installed SDK
  (`google-genai 2.20.0`, `AsyncModels.embed_content`).
- **`RETRIEVAL_MIN_SIMILARITY=0.15` was tuned for hashing.** Semantic models
  give unrelated text much higher cosine scores, so with gemini the floor will
  probably let irrelevant passages through until it is re-tuned against
  measured scores (Gap D, evaluation set).
- The column is fixed at `vector(512)`. A model that cannot shorten its output
  to 512 needs a new migration that changes the column width.
- Chunks from an inactive model stay stored but are unsearchable until
  `scripts.reindex` runs. That is deliberate, but a user's files can look
  "unsearchable" in the meantime. Nothing in the UI shows this yet (Gap B).
- A READY file whose text produces no chunks (whitespace only) is selected
  again on every run. It is harmless but not ideal.

**Environment variables**: `EMBEDDING_PROVIDER` (`hashing` | `gemini`),
`EMBEDDING_MODEL` (blank = `gemini-embedding-001`),
`EMBEDDING_TIMEOUT_SECONDS` (default 20), `GEMINI_API_KEY`.

**Manual steps still needed**
1. `cd backend && .venv/Scripts/alembic upgrade head` (dev database is at 0004).
2. Set `GEMINI_API_KEY` and `EMBEDDING_PROVIDER=gemini` in `backend/.env`.
3. `python -m scripts.reindex --dry-run`, then `python -m scripts.reindex`.
4. Ask a few relevant and unrelated questions, record their similarity scores,
   and set `RETRIEVAL_MIN_SIMILARITY` from the measurements.

**Next priority**: Gap B (background indexing jobs). It also gives the
re-index command a home in the worker and lets the UI show indexing state.

---

## 4. Test results

| When | Backend | Frontend |
|---|---|---|
| Audit baseline (before changes) | 492 passed | 156 passed |
| After Gap A | **531 passed** (492 + 39 new), 0 skipped, 3m11s | not re-run (no frontend changes) |

No backend linter or type checker is configured, so none was run. The
frontend was not modified.
