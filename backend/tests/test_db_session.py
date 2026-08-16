"""Tests for the database readiness check.

The golden rule for a health probe: **it must never raise, and it must
always return within a bounded time.** A probe that hangs is worse than one
that reports failure, because the orchestrator learns nothing.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import app.db.session as session_module
from app.db.session import check_database_connection


class TestCheckDatabaseConnection:
    async def test_reports_failure_when_nothing_is_listening(self) -> None:
        """A genuine failure path: no PostgreSQL is running during tests."""
        healthy, detail = await check_database_connection()

        assert healthy is False
        assert detail == "Database is unreachable"

    async def test_detail_never_leaks_connection_details(self) -> None:
        """SECURITY: the detail string is returned to unauthenticated
        callers, so it must not contain host, port, user, or database name."""
        _, detail = await check_database_connection()

        assert detail is not None
        for fragment in ("nexora_test", "localhost", "5432", "password"):
            assert fragment not in detail

    async def test_reports_success_when_the_ping_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def ping_ok() -> None:
            return None

        monkeypatch.setattr(session_module, "_ping_database", ping_ok)

        healthy, detail = await check_database_connection()

        assert healthy is True
        assert detail is None

    async def test_times_out_instead_of_hanging(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A database that accepts the connection but never answers must not
        stall the probe forever."""

        async def ping_hangs() -> None:
            await asyncio.sleep(30)

        monkeypatch.setattr(session_module, "_ping_database", ping_hangs)
        monkeypatch.setattr(
            session_module.get_settings(), "db_connect_timeout_seconds", 0.2
        )

        started = time.perf_counter()
        healthy, detail = await check_database_connection()
        elapsed = time.perf_counter() - started

        assert healthy is False
        assert detail == "Database is unreachable"
        assert elapsed < 5, f"probe took {elapsed:.1f}s; it should be bounded"

    async def test_never_raises_on_an_unexpected_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even a programming error inside the ping must surface as a
        'degraded' report rather than a 500 from the health endpoint."""

        async def ping_explodes() -> None:
            raise RuntimeError("something entirely unexpected")

        monkeypatch.setattr(session_module, "_ping_database", ping_explodes)

        healthy, _ = await check_database_connection()

        assert healthy is False
