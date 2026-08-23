"""Async database engine and session handling.

Key ideas, because these are the ones worth being able to explain:

**Engine** -- one per application. It owns a *connection pool*: a set of
reusable TCP connections to PostgreSQL. Opening a database connection takes
milliseconds, which is an eternity if you do it per request, so we open a few
and hand them out repeatedly.

**Session** -- one per request. It is the unit of work: it tracks the objects
you have loaded or changed and writes them out in a single transaction.
Sharing one session across requests would leak one user's data into another
user's transaction, so each request gets its own.

**Why async** -- while one request waits on the database, the event loop
serves other requests. A synchronous driver would block the entire process.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache
def get_engine() -> AsyncEngine:
    """Create (once) and return the async engine.

    Built lazily so that merely importing this module does not require valid
    database settings -- which keeps unit tests that never touch the database
    fast and independent.

    Note that creating an engine does *not* open a connection. The first
    connection is established when a query actually runs.
    """
    settings = get_settings()
    return create_async_engine(
        str(settings.database_url),
        echo=settings.db_echo,  # logs every SQL statement; noisy, dev only
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Verify a pooled connection is still alive before handing it out.
        # Without this, a connection dropped by a restart or idle timeout
        # surfaces as a random error on an unrelated request.
        pool_pre_ping=True,
        connect_args={"timeout": settings.db_connect_timeout_seconds},
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the factory that produces per-request sessions."""
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        # Keep attributes readable after commit(). With the default (True),
        # SQLAlchemy expires every object on commit, so touching an attribute
        # afterwards triggers a fresh query -- which in async code raises a
        # confusing lazy-load error while serialising the response.
        expire_on_commit=False,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session per request.

    Used as ``db: AsyncSession = Depends(get_db)``. FastAPI runs the code up
    to ``yield`` before the route handler, and the rest after the response --
    guaranteeing the session is closed even if the handler raises.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            # Never leave a half-finished transaction on the connection when
            # it returns to the pool.
            await session.rollback()
            raise


def get_db_session_factory() -> async_sessionmaker[AsyncSession]:
    """FastAPI dependency that yields the session *factory*, not a session.

    Streaming endpoints need this. Their response body is produced after the
    request scope has closed, so the per-request session from `get_db` is
    already gone by the time they need to write anything. They open a session
    of their own instead.

    It is a dependency rather than a direct call to `get_session_factory()`
    for one practical reason: tests override it exactly as they override
    `get_db`, so a streaming endpoint writes to the test database instead of
    quietly reaching past the override into the real one.
    """
    return get_session_factory()


async def _ping_database() -> None:
    """Run the cheapest possible query that proves the database answers."""
    engine = get_engine()
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def check_database_connection() -> tuple[bool, str | None]:
    """Ping the database for the readiness probe.

    Returns ``(healthy, detail)``. ``detail`` is a short, non-sensitive
    reason on failure.

    The whole check is wrapped in ``asyncio.wait_for`` so the probe is
    *bounded* no matter how the driver behaves internally. This is not
    theoretical: when nothing is listening on the database port, asyncpg
    resolves ``localhost`` to both ::1 and 127.0.0.1 and retries, taking ~4
    seconds to report a connection that was refused instantly. A load
    balancer whose probe times out at 2s would conclude a perfectly healthy
    API was dead. The driver's own connect timeout does not cover that
    retry loop; this does.

    SECURITY: the raw exception is logged server-side but never returned to
    the caller, because connection errors routinely contain the host, port,
    username and database name.
    """
    timeout = get_settings().db_connect_timeout_seconds
    try:
        await asyncio.wait_for(_ping_database(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Database readiness check timed out after %.1fs", timeout)
        return False, "Database is unreachable"
    except (SQLAlchemyError, OSError) as exc:
        # OSError is caught explicitly because a refused TCP connection
        # surfaces as a bare ConnectionRefusedError from the socket layer,
        # before SQLAlchemy gets a chance to wrap it.
        logger.warning("Database readiness check failed: %s", exc)
        return False, "Database is unreachable"
    except Exception as exc:  # noqa: BLE001 - a probe must never raise
        logger.warning("Database readiness check failed unexpectedly: %s", exc)
        return False, "Database is unreachable"
    return True, None


async def dispose_engine() -> None:
    """Close every pooled connection. Called on application shutdown."""
    engine = get_engine()
    await engine.dispose()
    logger.info("Database connection pool closed")
