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
import shutil
import tempfile
from pathlib import Path

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

# Offline, free and deterministic, whatever the developer's `.env` selects.
# A `.env` with EMBEDDING_PROVIDER=gemini and a real key once made the
# PostgreSQL tests call the billed Gemini API -- slow, nondeterministic, and
# failing when the event loop closed mid-request. Keys are blanked so that no
# code path can reach a real provider even by accident.
os.environ["AI_PROVIDER"] = "echo"
os.environ["AI_MODEL"] = "claude-opus-5"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["EMBEDDING_PROVIDER"] = "hashing"
os.environ["EMBEDDING_MODEL"] = ""
os.environ["IMAGE_READER"] = "none"
# The retrieval tests' expectations are written against these values; a
# developer tuning their own floor (0.62 for gemini) must not change them.
os.environ["RETRIEVAL_MIN_SIMILARITY"] = "0.15"
os.environ["RETRIEVAL_TOP_K"] = "5"
os.environ["CHUNK_SIZE_CHARACTERS"] = "1200"
os.environ["CHUNK_OVERLAP_CHARACTERS"] = "200"

# Uploads go to a throwaway directory, never to the real `backend/var/uploads`.
# Without this the suite would leave files behind on the developer's machine
# and, worse, tests would see each other's uploads.
TEST_UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="nexora-test-uploads-"))
os.environ["UPLOAD_DIR"] = str(TEST_UPLOAD_DIR)
# ------------------------------------------------------------------------

import asyncio  # noqa: E402
from collections.abc import AsyncGenerator, Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy import text as sa_text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: F401,E402  registers models on Base.metadata
from app.api.routes.auth import reset_auth_rate_limits  # noqa: E402
from app.api.routes.chat import reset_chat_rate_limits  # noqa: E402
from app.api.routes.files import reset_upload_rate_limits  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db, get_db_session_factory  # noqa: E402
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
    reset_chat_rate_limits()
    reset_upload_rate_limits()


@pytest.fixture(autouse=True)
def clean_upload_dir() -> Iterator[None]:
    """Empty the upload directory after each test.

    Files on disk are not part of the database transaction, so nothing else
    rolls them back. Without this a test that counts stored files would be
    measuring every previous test's uploads too.
    """
    yield
    if TEST_UPLOAD_DIR.exists():
        for leftover in TEST_UPLOAD_DIR.iterdir():
            if leftover.is_file():
                leftover.unlink(missing_ok=True)
            else:
                shutil.rmtree(leftover, ignore_errors=True)


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
    # Streaming endpoints open their own session after the request scope
    # closes, so they ask for the factory rather than a session. Without this
    # override they would reach past the test database into the real one.
    application.dependency_overrides[get_db_session_factory] = lambda: session_factory
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


# --- PostgreSQL fixtures, for the parts SQLite genuinely cannot test --------
#
# Vector similarity search is pgvector, and pgvector is PostgreSQL. Emulating
# `<=>` in Python would risk the suite passing against a reimplementation while
# production ran something different -- which is the failure mode a test is
# supposed to prevent. So these tests use a real database, and skip with a
# clear reason when one is not available rather than silently not running.

#: Overridable so CI can point at its own instance. The default matches
#: docker-compose.yml, which is what a developer here already has running.
TEST_PG_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://nexora:nexora_dev_password@localhost:5432/nexora_test",
)


async def _postgres_available(url: str) -> str | None:
    """Return None if usable, otherwise a reason to skip."""
    from sqlalchemy.ext.asyncio import create_async_engine as _create

    admin_url = url.rsplit("/", 1)[0] + "/postgres"
    try:
        admin = _create(admin_url, isolation_level="AUTOCOMMIT")
        database = url.rsplit("/", 1)[1]
        async with admin.connect() as connection:
            existing = await connection.execute(
                sa_text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database},
            )
            if existing.scalar() is None:
                # Identifier, not a bind parameter -- CREATE DATABASE takes no
                # placeholders. The name comes from our own configuration, and
                # is quoted so an odd one cannot break the statement.
                await connection.execute(sa_text(f'CREATE DATABASE "{database}"'))
        await admin.dispose()
    except Exception as error:  # noqa: BLE001 - any failure means "skip"
        return f"PostgreSQL not available at {admin_url}: {error}"

    try:
        engine = _create(url)
        async with engine.begin() as connection:
            await connection.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))
        await engine.dispose()
    except Exception as error:  # noqa: BLE001
        return f"pgvector extension unavailable: {error}"

    return None


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """The test database URL, or skip the whole module."""
    reason = asyncio.run(_postgres_available(TEST_PG_URL))
    if reason:
        pytest.skip(reason, allow_module_level=True)
    return TEST_PG_URL


@pytest.fixture
async def pg_engine(postgres_url: str) -> AsyncGenerator[AsyncEngine, None]:
    """A real PostgreSQL engine with a freshly created schema.

    Tables are dropped and recreated per test rather than wrapped in a rolled
    back transaction, because pgvector index behaviour is part of what is under
    test and index state does not roll back cleanly.
    """
    engine = create_async_engine(postgres_url)

    async with engine.begin() as connection:
        await connection.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def pg_db(
    pg_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with pg_session_factory() as session:
        yield session


@pytest.fixture
def pg_app(
    settings: Settings, pg_session_factory: async_sessionmaker[AsyncSession]
) -> FastAPI:
    """The application wired to PostgreSQL instead of SQLite."""
    application = create_app(settings)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with pg_session_factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_db_session_factory] = lambda: pg_session_factory
    return application


@pytest.fixture
async def pg_client(pg_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=pg_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:5173"},
    ) as async_client:
        yield async_client
