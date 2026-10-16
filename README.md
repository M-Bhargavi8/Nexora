# NEXORA

**An AI workspace where you upload your documents and ask questions about them. Answers stream
back in real time and cite the passages they came from.**

NEXORA is a full-stack retrieval-augmented generation (RAG) application. It has an async FastAPI
backend, PostgreSQL with pgvector, a provider-independent AI gateway (Anthropic Claude or Google
Gemini), and a React + TypeScript frontend. I built it in phases, and every phase is verified
before the next one starts.

> **Status: phases 0–6 complete.** That covers the foundation, authentication, streaming chat,
> conversation management, file upload and RAG. The Google Gemini provider was added after
> phase 6. Tools, model routing, memory and agents (phases 7–15) are not built yet. See
> [`ROADMAP.md`](./ROADMAP.md) for each phase's verification evidence.

---

## Features

**Chat**
- Replies stream token by token over server-sent events (SSE). Markdown and code are rendered,
  and model output is treated as untrusted, so raw HTML is never rendered.
- Stop a reply mid-stream, or regenerate the last one.
- Conversations can be renamed, deleted, searched and paginated, and you can edit a message
  you sent.

**Documents and RAG**
- Upload PDF, DOCX, XLSX, CSV, TXT and Markdown files, plus PNG, JPEG, GIF and WebP images. The
  server extracts text from the documents. Images are stored, but no text is extracted from them.
- Text is split into overlapping chunks and stored with vectors in PostgreSQL via **pgvector**.
- When you ask a question, the most similar chunks go into the prompt labelled `[Source N]`. The
  model cites those labels, and the UI lists the source files the answer drew on.

**AI gateway**
- One interface, `ModelProvider`, with three adapters: **Anthropic Claude**, **Google Gemini**,
  and an offline **echo** provider. The echo provider lets the whole app run and be tested
  without an API key.
- Embeddings use a separate interface, `EmbeddingProvider`, because chat and embeddings are
  bought separately (Anthropic sells no embedding model).

## Security

- **Passwords** are hashed with Argon2id. Sessions use **httpOnly cookies** with a configurable
  `SameSite` setting, and an insecure `SameSite=None` without `Secure` is rejected at startup.
- **CSRF:** state-changing requests are checked against an `Origin` allowlist.
- **Rate limiting** applies per IP and per account on login, and also on chat and uploads.
- **Upload validation** checks the file's actual bytes (magic numbers), not its extension or
  the client's claimed content type. Size is enforced while streaming, and the PDF page count
  and extracted text length are capped.
- **No default secrets:** the app refuses to start without a `SECRET_KEY`, and the dev
  database is bound to `127.0.0.1` only.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite, Tailwind CSS, React Router, react-markdown |
| Backend | Python 3.11, FastAPI, Pydantic, SQLAlchemy 2 (async, asyncpg), Alembic |
| Database | PostgreSQL 17 + pgvector, run in Docker |
| AI | Anthropic Claude and Google Gemini SDKs behind a common gateway |
| Testing | pytest (492 backend tests), Vitest + React Testing Library (156 frontend tests) |

## Architecture

```text
React (Vite)  ──HTTP/SSE──▶  FastAPI
                               ├── api/routes/   auth · chat · files · health
                               ├── services/     auth · chat · upload · extraction · chunking · retrieval · storage
                               ├── ai/           gateway → providers (anthropic · gemini · echo)
                               │                 embeddings (hashing)
                               └── db/ models/   SQLAlchemy async ──▶ PostgreSQL + pgvector
```

A question travels through the system like this: the chat route asks the retrieval service for
the top-k chunks from the user's own files, builds the prompt with those passages, and streams
the model's reply back over SSE, sending the list of sources first. The reply is saved when the
stream ends.

## Run it locally

**Prerequisites:** Python 3.11+, Node.js 20.19+ and Docker Desktop.

```bash
# 1. Database (PostgreSQL + pgvector)
docker compose up -d

# 2. Backend
cd backend
python -m venv .venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env                 # macOS/Linux: cp; then set SECRET_KEY (below)
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# 3. Frontend (new terminal)
cd frontend
npm install
copy .env.example .env
npm run dev
```

To generate a `SECRET_KEY`, run:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Choosing a model.** `AI_PROVIDER` defaults to `echo`, which repeats your message back so
everything works offline. To use a real model, set `AI_PROVIDER=anthropic` with
`ANTHROPIC_API_KEY`, or `AI_PROVIDER=gemini` with `GEMINI_API_KEY`, in `backend/.env`.

| What | URL |
|---|---|
| App | http://localhost:5173 |
| API health | http://localhost:8000/api/v1/health |
| API docs (dev only) | http://localhost:8000/docs |

## Tests

```bash
cd backend && python -m pytest       # 492 tests, using in-memory SQLite (no Docker needed)
cd frontend && npm test              # 156 tests
cd frontend && npm run build         # type-check and production build
```

The backend tests use in-memory SQLite, so they cover the application logic. PostgreSQL-specific
behaviour, such as pgvector similarity search, is verified manually against the Docker database
and recorded in `ROADMAP.md`.

## Known limitations

- **Retrieval is lexical, not semantic.** The only embedding provider is a hashing vectoriser,
  so it matches shared words, not meaning. A semantic embedding model is the next upgrade.
  Changing `EMBEDDING_PROVIDER` means re-embedding the stored chunks.
- **No OCR.** Text inside images and scanned PDFs is not extracted.
- **Not deployed.** It runs locally with Docker for the database only.

## Roadmap

Phases 7–15 add tools (calculator, web search, Python), a cost- and latency-aware model router,
user-controlled memory, research and coding agents, evaluation, and production deployment. See
[`ROADMAP.md`](./ROADMAP.md).

## Author

**Bhargavi Mudumbha**: [LinkedIn](https://www.linkedin.com/in/bhargavi-mudumbha) · [GitHub](https://github.com/M-Bhargavi8)
