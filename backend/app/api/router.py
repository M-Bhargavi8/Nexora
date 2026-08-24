"""Aggregates every versioned route module into one router.

`main.py` mounts this single router, so adding a new resource means adding one
`include_router` line here rather than editing application startup.

The `/api/v1` prefix is applied by `main.py`. Versioning the API from day one
means that when a breaking change is needed later, `/api/v2` can be added
alongside `/api/v1` while existing clients keep working.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import auth, chat, files, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(files.router)
