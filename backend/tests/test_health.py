"""Tests for the health endpoints and the request-context middleware."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

import app.api.routes.health as health_route

API = "/api/v1"


class TestLiveness:
    async def test_returns_200_and_expected_shape(self, client: AsyncClient) -> None:
        response = await client.get(f"{API}/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["app"] == "NEXORA"
        assert body["environment"] == "test"
        assert "version" in body

    async def test_does_not_touch_the_database(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Liveness must stay up even when the database is down.

        This is the whole reason liveness and readiness are separate, so it
        is worth asserting rather than assuming.
        """

        async def explode() -> None:
            raise AssertionError("liveness must not call the database")

        monkeypatch.setattr(health_route, "check_database_connection", explode)

        response = await client.get(f"{API}/health")
        assert response.status_code == 200


class TestReadiness:
    async def test_reports_degraded_with_503_when_database_is_down(
        self, client: AsyncClient
    ) -> None:
        """No database is running during this test, so this exercises the
        real failure path rather than a mock."""
        response = await client.get(f"{API}/health/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"

        database_check = next(c for c in body["checks"] if c["name"] == "database")
        assert database_check["healthy"] is False
        assert database_check["detail"] == "Database is unreachable"

    async def test_does_not_leak_connection_details_on_failure(
        self, client: AsyncClient
    ) -> None:
        """SECURITY: the error must not expose host, port, user, or password."""
        response = await client.get(f"{API}/health/ready")

        raw_body = response.text.lower()
        for secret_fragment in ("nexora_test", "password", "5432", "asyncpg"):
            assert secret_fragment not in raw_body

    async def test_reports_ready_with_200_when_database_is_up(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The healthy path, faked -- no real database is available here."""

        async def healthy() -> tuple[bool, str | None]:
            return True, None

        monkeypatch.setattr(health_route, "check_database_connection", healthy)

        response = await client.get(f"{API}/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["checks"][0]["healthy"] is True


class TestRequestId:
    async def test_response_carries_a_request_id(self, client: AsyncClient) -> None:
        response = await client.get(f"{API}/health")

        assert response.headers.get("X-Request-ID")

    async def test_client_supplied_id_is_echoed_back(self, client: AsyncClient) -> None:
        response = await client.get(
            f"{API}/health", headers={"X-Request-ID": "trace-abc-123"}
        )

        assert response.headers["X-Request-ID"] == "trace-abc-123"

    async def test_unsafe_characters_are_stripped(self, client: AsyncClient) -> None:
        """SECURITY: only a safe character set survives into the logs.

        The newline / log-injection case is covered directly in
        `test_middleware.py`, because HTTP clients reject raw newlines in a
        header before the request ever reaches the application.
        """
        response = await client.get(
            f"{API}/health",
            headers={"X-Request-ID": "abc def!@#$%"},
        )

        assert response.headers["X-Request-ID"] == "abcdef"

    async def test_overlong_id_is_truncated(self, client: AsyncClient) -> None:
        response = await client.get(
            f"{API}/health", headers={"X-Request-ID": "a" * 500}
        )

        assert len(response.headers["X-Request-ID"]) == 64


class TestApiSurface:
    async def test_unknown_route_returns_404(self, client: AsyncClient) -> None:
        response = await client.get(f"{API}/does-not-exist")
        assert response.status_code == 404

    async def test_openapi_schema_is_available_outside_production(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/openapi.json")

        assert response.status_code == 200
        assert f"{API}/health" in response.json()["paths"]
