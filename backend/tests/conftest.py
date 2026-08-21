"""Shared pytest fixtures.

Environment variables are set at the very top, *before* any application module
is imported. Configuration is read at import time, so setting them later would
be too late and the tests would silently pick up the developer's real `.env`
instead -- making results depend on whose machine they run on.

**About the test database.** These tests run against in-memory SQLite, not
PostgreSQL, so the full authentication flow can be exercised without Docker.
The application itself always uses PostgreSQL; only this fixture differs.

That trade-off is worth stating explicitly. SQLite verifies our *logic* --
is the password hashed, is a duplicate email rejected, does logout revoke the
session -- because none of that is engine-specific. It does **not** verify
PostgreSQL-specific behaviour such as native UUID handling, timezone-aware
timestamp storage, or concurrent-insert behaviour under a unique index. Those
need a real PostgreSQL instance.
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

from collections.abc import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: F401,E402  registers models on Base.metadata
from app.api.routes.auth import reset_auth_rate_limits  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Application settings built from the test environment above."""
    get_settings.cache_clear()  # discard anything cached by an earlier import
    return get_settings()


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    """Clear every rate-limit counter between tests.

    The limiters are module-level state. Without this, a test that makes ten
    login attempts would exhaust the quota and cause unrelated later tests to
    fail with 429 -- the classic symptom of shared state between tests.

    Both the per-IP and per-account counters are cleared; missing either one
    would leave exactly this kind of cross-test bleed.
    """
    reset_auth_rate_limits()


@pytest.fixture
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """A fresh in-memory SQLite database per test.

    `StaticPool` forces every connection to reuse the *same* underlying
    connection. That matters for `:memory:`, where each new connection would
    otherwise get its own empty database and the test would mysteriously see
    no data.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # SQLite ignores foreign keys unless explicitly told not to. Without this
    # PRAGMA, ON DELETE CASCADE silently does nothing and a test asserting
    # that deleting a user removes their sessions would pass against
    # PostgreSQL but fail here -- or worse, the reverse.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """A database session for tests that call services directly."""
    async with session_factory() as session:
        yield session


@pytest.fixture
def app(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> FastAPI:
    """A fresh application instance wired to the test database.

    `dependency_overrides` is FastAPI's built-in seam for exactly this: the
    routes still declare `Depends(get_db)`, but the container hands them a
    test session instead. No production code is aware that tests exist --
    which is the point of dependency injection.
    """
    application = create_app(settings)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_get_db
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client that calls the app in-process.

    ASGITransport dispatches straight into the ASGI application, so no real
    network socket or running server is involved. The client keeps a cookie
    jar across requests, so a login in one call authenticates the next --
    exactly as a browser would.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        # Browsers send an Origin header on cross-origin requests; the CSRF
        # middleware checks it. Setting an allowed origin here makes the test
        # client behave like the real frontend.
        headers={"Origin": "http://localhost:5173"},
    ) as async_client:
        yield async_client
