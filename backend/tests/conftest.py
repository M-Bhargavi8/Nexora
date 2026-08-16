"""Shared pytest fixtures.

Environment variables are set at the very top, *before* any application module
is imported. Configuration is read at import time, so setting them later would
be too late and the tests would silently pick up the developer's real `.env`
instead -- making results depend on whose machine they run on.
"""

from __future__ import annotations

import os

# --- Must run before any `app.*` import ---------------------------------
# Real environment variables take priority over the `.env` file in
# pydantic-settings, so these values isolate the test suite from local config.
os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-long-enough-to-pass-validation-32"
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://nexora_test:nexora_test@localhost:5432/nexora_test"
)
os.environ["CORS_ORIGINS"] = "http://localhost:5173"
os.environ["LOG_LEVEL"] = "WARNING"  # keep test output readable
# ------------------------------------------------------------------------

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Application settings built from the test environment above."""
    get_settings.cache_clear()  # discard anything cached by an earlier import
    return get_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """A fresh application instance per test."""
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI):
    """An HTTP client that calls the app in-process.

    ASGITransport dispatches straight into the ASGI application, so no real
    network socket or running server is involved. Tests are therefore fast
    and cannot collide on a port.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
