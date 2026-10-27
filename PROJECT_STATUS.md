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
| Gap A — semantic embeddings | COMPLETE | Verified live against Gemini on 2026-10-27. See §3 |
| Chat upload button | COMPLETE | Upload from the chat box. See §3 |
| Duplicate uploads | COMPLETE | Identical re-uploads reuse the stored file; search drops repeated passages. See §3 |
| Files in chats, real answers, images | COMPLETE | Chat answers from files uploaded into it, using Gemini; images are read. Verified live. See §3 |
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

**Status: COMPLETE** (2026-10-27). Code, migration and tests pass, and the
embedding path was verified live against the Gemini API (below).

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
The developer then upgraded the dev database `nexora` to `0005` (2026-10-27).

**Verified live (2026-10-27)**, using the developer key from `backend/.env`
(never printed):
- `gemini-embedding-001` is in the key's catalogue and supports
  `embedContent`. NEXORA's adapter returned 512-dimension vectors with the
  identity `gemini:gemini-embedding-001:512`.
- Probe with 4 passages and 8 questions: **4 of 4 relevant questions ranked the
  right passage first**, including "I forgot my login, how do I get back in?"
  matching a password-reset passage with almost no shared words. Correct
  matches scored **0.671–0.712**; unrelated questions scored **0.478–0.564** at
  best; the highest score for any wrong passage was 0.582.
- `RETRIEVAL_MIN_SIMILARITY=0.62` was set in the developer's `.env`, inside
  that gap.
- End to end in the running app, with a throwaway account that was deleted
  afterwards: a text file uploaded from the new chat button became "Ready"; the
  login question was answered from it at a **69% match**; "What is the capital
  of France?" got **no sources**; the stored chunk carried the Gemini identity.

**Tests**: see §4.

**Known limitations**
- **The 0.62 floor rests on 8 questions.** It is a measured starting point,
  not a validated one. Gap D's evaluation set must re-check it on more data.
- The default stays `0.15` because it suits `hashing`, the default
  provider. Anyone switching to gemini must also set the floor; `.env.example`
  says so. A per-provider default would remove that trap.
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

**Manual steps still needed**: none on this machine. Any other environment
needs `alembic upgrade head`, `GEMINI_API_KEY`, `EMBEDDING_PROVIDER=gemini`,
`RETRIEVAL_MIN_SIMILARITY=0.62`, and `python -m scripts.reindex`.

### Chat upload button (2026-10-27)

**Status: COMPLETE.** Requested by the developer: upload from beside the chat
input instead of only through the separate Files page. The Files page and its
header link are unchanged.

- A paperclip button sits to the left of the message box and opens the file
  picker, with the same accepted types.
- It uses the same `uploadFile` request and the same local checks
  (`localRejectionReason`, now exported from `useFiles`) as the Files page,
  so the rules cannot drift apart. The server still validates every byte.
- A status chip per file shows "Uploading N%", "Ready — ask about it", "Stored,
  but it has no readable text", or the server's refusal reason. Finished
  chips can be dismissed.
- No "attach to message" step: retrieval already searches every file the user
  owns.

**Files**: `src/components/Composer.tsx`, `src/pages/ChatPage.tsx`,
`src/hooks/useFiles.ts`; new `src/hooks/useChatUploads.ts` and
`src/components/UploadStatusList.tsx`; 6 tests added to
`src/pages/ChatPage.test.tsx`.

### Duplicate uploads (2026-10-27)

**Status: COMPLETE.** Found in use: the developer's resume PDF was stored
three times, so every search returned the same passage three times and crowded
out everything else.

**Implemented**
- `save_upload` computes SHA-256 while streaming (no second read) and stores
  it in the new `files.content_sha256`.
- `create_file`: after the type checks pass, if **this user** already has a
  file with the same hash, the new bytes are deleted and the existing file is
  returned with `reused=True`. The route answers **200** instead of 201,
  because nothing was created. A reused file gets the same chance to be
  indexed as a new upload, if it is missing chunks from the active model.
- Type checks run *before* the duplicate check, so a stored file renamed to a
  mismatched extension is still refused (400), not matched to its twin.
- Matching is per user only. Matching across users would tell one person that
  another holds the same document.
- `search_chunks` reads 3× the limit and drops passages whose text, with
  whitespace collapsed, has already been kept. It still returns up to `limit`
  **distinct** passages, keeping the closest copy. This also covers the same
  text in two different files (for example, a PDF and the DOCX it came from).
- `python -m scripts.backfill_file_hashes [--dry-run]` hashes older files from
  their bytes. It is idempotent, reports missing bytes, and **reports but never
  deletes** existing duplicate groups.
- Frontend: `uploadFile` accepts 200 as success. The chat chip says "Already
  uploaded — using your existing copy". The Files page shows a notice instead
  of listing the file twice.
- Test isolation fix: `tests/conftest.py` now pins the AI provider, embedding
  provider, API keys, similarity floor and chunk sizes. A developer `.env`
  with `EMBEDDING_PROVIDER=gemini` had made the PostgreSQL tests call the
  billed Gemini API and fail.

**Migration `0006_file_content_sha256`**: nullable `varchar(64)` and a
non-unique index `(user_id, content_sha256)`. Non-unique on purpose, because
older rows may already be duplicates. Verified on a throwaway database:
- upgrade worked;
- `alembic check` reported no drift;
- downgrade kept the rows;
- re-upgrading worked.
Then applied to the dev database and back-filled: 3 files hashed, **1 duplicate
group** reported (the three resume copies), and none deleted.

**Verified against real data and the running app**: on the developer's
resume, "Where does Bhargavi work?" returned passages #0, #2 and #3 (it
previously returned #0 three times). In the browser, with a throwaway account
that was deleted afterwards:
- the first upload showed "Ready";
- the same bytes under a new name showed "Already uploaded — using your
  existing copy";
- the server listed exactly one file.

**Known limitations**
- Two *simultaneous* uploads of the same bytes can both be stored, because the
  check and the insert are not atomic. A unique index would close this, but
  existing duplicates would have to be removed first.
- Re-uploading keeps the original filename; the new name is not recorded.
- Near-duplicates (the same text with an edited word, or a re-saved PDF with
  different bytes) are separate files. Search de-duplication covers exact
  passage repeats only.

**Files**: backend `app/services/storage.py`, `app/services/files.py`,
`app/services/retrieval.py`, `app/models/file.py`, `app/schemas/file.py`,
`app/api/routes/files.py`, `tests/conftest.py`, `tests/test_files_api.py`;
new `alembic/versions/0006_add_file_content_sha256.py`,
`scripts/backfill_file_hashes.py`, `tests/test_duplicates.py`. Frontend
`src/lib/files.ts`, `src/hooks/useFiles.ts`, `src/hooks/useChatUploads.ts`,
`src/components/UploadStatusList.tsx`, `src/pages/FilesPage.tsx`, and tests in
`FilesPage.test.tsx` and `ChatPage.test.tsx`.

### Files in chats, real answers, image reading (2026-10-27)

**Status: COMPLETE.** Requested by the developer: "files, documents or
images should be uploaded to the chat and it should answer everything we ask".

**Why it did not before**: chat ran on the offline echo provider; a file
uploaded in a chat only joined general search, whose 0.62 floor rejects
questions like "what is my email?" (it scored 0.556 against the resume); and
images were stored unread.

**Implemented**
- **Files belong to the chat they were uploaded in.** There is a new table,
  `conversation_files`, and `POST /chat` accepts `attach_file_ids`. Only
  the caller's own files are attached; other ids are silently ignored, so the
  endpoint cannot be used to probe which ids exist. Conversation detail lists
  its `files`, and `DELETE /chat/conversations/{id}/files/{file_id}` removes
  one (the file stays in the library).
- **Answering from the chat's files first.** Send, regenerate and edit all
  take up to `CONVERSATION_FILE_TOP_K` (6) passages from the chat's files
  **without** the similarity floor, then fill from the rest of the library at
  the normal floor, with repeats removed. The question is embedded **once**
  (`embed_query`) and the vector shared by both searches.
- **Stream idle timeout** (`AI_STREAM_IDLE_TIMEOUT_SECONDS`, 90) wraps every
  provider's stream (`with_idle_timeout`). It exists because
  `gemini-flash-latest` took **524 s** to answer one probe.
- **Image reading** (`IMAGE_READER=gemini`, model
  `gemini-3-flash-preview`). It runs only after Pillow has validated the
  image. The model transcribes the text verbatim and then describes the image;
  the result becomes the file's text (status `ready`, with a note that an AI
  model produced it), so the image is chunked, embedded and searchable. A
  failure keeps the image stored and unread. The output is treated as
  untrusted document text, and the prompt tells the model not to follow
  instructions found in the image.
- **Frontend.** An upload made in the chat is *staged* ("Ready — added with
  your next message"). Send waits while an upload is in progress. Sent files
  appear in an "In this chat" row with × to remove them, and the row reloads
  with the conversation.
- **Config.** `AI_PROVIDER=gemini`, `AI_MODEL=gemini-3-flash-preview` and
  `IMAGE_READER=gemini` were appended to the developer's `backend/.env`
  (the key was not read). `.env.example` documents every new setting and no
  longer suggests the unverified `gemini-3.7-flash`.

**Model availability, measured 2026-10-27 with this key**:
- `gemini-3-flash-preview` streams correctly (first text after 2–3.5 s).
- `gemini-2.5-flash`, `-flash-lite` and `-pro` are listed for the key but
  return **404** when called.
- `gemini-flash-latest` returned 503 once and took 524 s another time.

**Migration `0007_conversation_files`**: composite PK
`(conversation_id, file_id)`, both foreign keys `ON DELETE CASCADE`, plus an
index on `file_id`. Verified on a throwaway database:
- upgrade worked;
- `alembic check` reported no drift;
- downgrade dropped the table;
- re-upgrading worked.
Then applied to the dev database.

**Verified end to end with real Gemini** (the whole app in-process, against a
throwaway database that was dropped afterwards, using a fictional resume):
- "what is my email?" scored 0.53 (below the floor) and was answered
  correctly from the attached resume;
- a follow-up in the same chat ("Where did I study…") was answered
  correctly;
- "capital of France?" said the document does not cover it, then answered
  "Paris";
- a generated receipt **PNG** was read in 4.9 s, and "how much did I pay /
  how many croissants" was answered "15.60 EUR, 3".

**Known limitations**
- A chat's files always reach the model, so an unrelated question still
  shows "Answered using 1 passage" (the France case), even though the answer
  correctly ignored it. Labelling these passages as "from this chat" would be
  more honest.
- Images are read once, at upload. Questions are answered from that text, not
  from the pixels, so a detail the description missed cannot be recovered
  without re-reading the image.
- There is no OCR for scanned PDFs yet. Their pages are not sent to the image
  reader.
- Files uploaded before this change belong to no chat. Uploading one again in
  a chat reuses the stored copy (duplicate detection) and attaches it.
- `gemini-3-flash-preview` is a *preview* model, which Google can change or
  withdraw. Re-run `scripts.verify_gemini` if replies start failing.
- Cost: every message is a paid chat call, plus one embedding call; every
  image upload is one more call.

**Files**: backend `app/ai/base.py`, `app/ai/gateway.py`,
`app/ai/providers/gemini_provider.py`, `app/core/config.py`,
`app/services/chat.py`, `app/services/files.py`, `app/services/retrieval.py`,
`app/schemas/chat.py`, `app/api/routes/chat.py`, `app/models/__init__.py`,
`tests/conftest.py` (now also pins `IMAGE_READER=none`), `.env.example`;
new `app/models/conversation_file.py`,
`alembic/versions/0007_create_conversation_files.py`,
`tests/test_conversation_files.py` (14), `tests/test_image_reading.py` (17).
Frontend `src/lib/chat.ts`, `src/hooks/useChat.ts`,
`src/hooks/useChatUploads.ts`, `src/components/Composer.tsx`,
`src/components/UploadStatusList.tsx`, `src/pages/ChatPage.tsx`, new
`src/components/ChatFileList.tsx`, and 6 tests in `ChatPage.test.tsx`.

**Next priority**: Gap B (background indexing jobs). It also gives the
re-index command a home in the worker and lets the UI show indexing state.

---

## 4. Test results

| When | Backend | Frontend |
|---|---|---|
| Audit baseline (before changes) | 492 passed | 156 passed |
| After Gap A | **531 passed** (492 + 39 new), 0 skipped, 3m11s | not re-run (no frontend changes) |
| After chat upload button | unchanged (no backend changes) | **162 passed** (156 + 6 new); `oxlint` clean; `npm run build` succeeds |
| After duplicate uploads | **548 passed** (+17) | **164 passed** (+2); `oxlint` clean; build succeeds |
| After files in chats + images | **579 passed** (+31), offline, 5m15s | **170 passed** (+6); `oxlint` clean; build succeeds |

No backend linter or type checker is configured, so none was run.
