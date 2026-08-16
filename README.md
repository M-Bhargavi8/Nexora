# NEXORA

**NEXORA — Your Intelligent AI Workspace**

NEXORA is an original, professionally-architected AI application that combines
large language models, retrieval, tools, memory, and agents into a single
workspace. It is not a clone of any existing product's UI, branding, or
proprietary implementation — it is our own system, built incrementally so that
every layer is understood before the next one is added.

> **Status: Phase 1 — Foundation, complete.**
> A working vertical slice: a React page that calls the FastAPI backend, which
> reports whether it can reach PostgreSQL. Configuration, structured logging,
> async database sessions, migrations, and a 45-test backend suite are in
> place. No AI features yet — those begin at Phase 3.
> See [`ROADMAP.md`](./ROADMAP.md) for what comes next.

---

## 1. What NEXORA Will Eventually Do

- Chat with streaming responses, markdown, and code rendering
- Understand and analyze uploaded documents, images, and data files
- Answer questions using retrieval-augmented generation (RAG) over private
  knowledge
- Search the web and cite sources
- Use tools (calculator, web search, Python/data analysis, code analysis)
- Route requests to the right model for the job (cost/latency/capability aware)
- Remember useful context across a conversation, a project, and a user
  (fully user-controlled: view, edit, delete, disable)
- Run multi-step agent tasks with a visible, non-hidden activity summary
- Support project/workspace organization for files, chats, and instructions
- Eventually plan and generate small software projects from natural language

We are building toward this vision in stages — see `ROADMAP.md`. Nothing in
this list is implemented yet.

## 2. Why NEXORA Exists

This is a learning project first, a product second. The goal is to end up
with a system that:

1. Actually works, end to end, in production-realistic conditions.
2. Was built by understanding every important decision, not by pasting a
   finished app — every phase comes with an explanation before the code.
3. Demonstrates real engineering judgment (security, cost control, testing,
   observability) that holds up in a technical interview.

## 3. Technology Stack (Planned)

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript + Vite + Tailwind CSS + shadcn/ui + Lucide Icons |
| Backend | Python + FastAPI + Pydantic + SQLAlchemy + Alembic |
| Database | PostgreSQL + pgvector |
| Cache / background jobs | Redis + background worker |
| AI | Provider-independent Model Gateway (no single vendor lock-in) |
| Testing | Pytest (backend), a React testing library (frontend) |
| Deployment (later) | Docker, reverse proxy, CI/CD |

Nothing here is installed or wired up yet — this table documents the target.
Python, Node, npm and git **are** present on the dev machine and were verified
by running them; **PostgreSQL and Docker are not installed yet** and must be
resolved before Phase 1's database work. See `ARCHITECTURE.md` §1
"Environment Check" for the verified inventory and the open decision on how we
run PostgreSQL.

## 4. Repository Structure (Target)

```text
nexora/
├── README.md               # This file
├── ARCHITECTURE.md         # System design, data flow, security model
├── ROADMAP.md              # Phase-by-phase build plan and status
├── docker-compose.yml      # PostgreSQL + pgvector for local development
├── infra/
│   └── postgres/init/      # Runs once on first database creation
├── backend/                # FastAPI application
│   ├── app/
│   │   ├── api/routes/     # HTTP endpoints
│   │   ├── core/           # Config, logging, middleware
│   │   ├── db/             # Engine, sessions, ORM base
│   │   ├── schemas/        # Pydantic request/response models
│   │   └── main.py         # Application factory
│   ├── alembic/            # Database migrations
│   ├── tests/
│   └── requirements.txt
└── frontend/               # React + TypeScript + Vite app
    └── src/
        ├── components/     # Presentational components
        ├── hooks/          # Data-loading logic
        └── lib/api.ts      # Typed backend client
```

Everything above exists as of Phase 1. Directories arrive as the phase that
needs them arrives — there are no empty placeholder folders.

## 5. How to Run NEXORA

**Prerequisites:** Python 3.11+, Node.js 20.19+, and Docker Desktop.

### First-time setup

```bash
# 1. Database
docker compose up -d

# 2. Backend
cd backend
python -m venv .venv
.venv\Scripts\activate                 # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env                 # then set SECRET_KEY (see below)
alembic upgrade head

# 3. Frontend
cd ../frontend
npm install
copy .env.example .env
```

Generate a `SECRET_KEY` for `backend/.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Every day

Three terminals:

```bash
docker compose up -d
```

```bash
cd backend && .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm run dev
```

| What | URL |
|---|---|
| Frontend | http://localhost:5173 |
| API health | http://localhost:8000/api/v1/health |
| API docs (dev only) | http://localhost:8000/docs |

### Tests

```bash
cd backend && .venv\Scripts\python.exe -m pytest -v
```

### Troubleshooting

- **"Database unavailable" on the status page** — the API is fine and
  correctly reporting the database is down. Run `docker compose up -d`.
- **"Cannot reach the API"** — the backend isn't running, or `VITE_API_URL`
  in `frontend/.env` points somewhere else.
- **Backend won't start, `SECRET_KEY` error** — expected. Copy
  `.env.example` to `.env` and set a key of at least 32 characters.

## 6. Development Philosophy

- **Incremental**: one phase at a time, explained before implemented.
- **Explainable**: every non-trivial component should be something you can
  describe in an interview — what it does, why it exists, what the
  alternatives were.
- **Secure by default**: authentication, input validation, and secrets
  handling are designed in from Phase 1, not bolted on later.
- **No overbuilding**: a feature is only added when the current phase
  actually needs it.

## 7. License / Ownership

Private learning/portfolio project. No license has been chosen yet.
