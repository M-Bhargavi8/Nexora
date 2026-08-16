# NEXORA

**NEXORA — Your Intelligent AI Workspace**

NEXORA is an original, professionally-architected AI application that combines
large language models, retrieval, tools, memory, and agents into a single
workspace. It is not a clone of any existing product's UI, branding, or
proprietary implementation — it is our own system, built incrementally so that
every layer is understood before the next one is added.

> **Status: Phase 0 — Architecture & Planning.**
> No application code has been written yet. This phase only establishes the
> plan, the repository shape, and the documentation. See [`ROADMAP.md`](./ROADMAP.md)
> for what comes next and [`ARCHITECTURE.md`](./ARCHITECTURE.md) for how the
> system is designed.
>
> **One decision is blocking Phase 1:** how we run PostgreSQL + pgvector, since
> neither PostgreSQL nor Docker is installed on the dev machine. The options and
> a recommendation are in `ARCHITECTURE.md` §1.3.

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
├── README.md              # This file
├── ARCHITECTURE.md        # System design, data flow, security model
├── ROADMAP.md             # Phase-by-phase build plan and status
├── .gitignore
├── docs/                  # Additional design notes, ADRs, diagrams
├── frontend/               # React + TypeScript + Vite app
│   └── src/
└── backend/                 # FastAPI application
    └── app/
```

This is the **target** layout. Today the repository contains only the four
Phase 0 documents plus `.gitattributes` — no `frontend/`, `backend/` or
`docs/` directory exists yet. Phase 1 creates them along with a working,
minimal app. (Empty directories are deliberately not created, because git
cannot track them and they would only be decoration.)

## 5. How to Run NEXORA

There is nothing to run yet. This section will be filled in at the end of
Phase 1 with concrete commands for starting the frontend dev server, the
backend API, and the database.

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
