"""Tests for CSRF origin checking."""

from __future__ import annotations

from httpx import AsyncClient

API = "/api/v1"
CREDENTIALS = {"email": "ada@example.com", "password": "a-sufficiently-long-password"}


class TestCsrfOriginCheck:
    async def test_blocks_a_state_changing_request_from_a_foreign_origin(
        self, client: AsyncClient
    ) -> None:
        """The core attack: a page on evil.example triggering a POST that
        rides along on the user's cookie."""
        response = await client.post(
            f"{API}/auth/login",
            json=CREDENTIALS,
            headers={"Origin": "https://evil.example"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Request origin is not allowed"

    async def test_allows_a_request_from_the_configured_frontend(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            f"{API}/auth/signup",
            json=CREDENTIALS,
            headers={"Origin": "http://localhost:5173"},
        )

        assert response.status_code == 201

    async def test_allows_a_request_with_no_origin_header(self, app) -> None:
        """Non-browser clients (curl, server-to-server) send no Origin.
        CSRF is a browser-only attack and a browser cannot be made to omit
        the header cross-origin, so there is nothing to block here.

        A dedicated client is built here because the shared `client` fixture
        sets a default Origin to imitate the browser; setting it to "" would
        not remove the header, only blank it -- and a blank origin is *not*
        the same as an absent one.
        """
        from httpx import ASGITransport, AsyncClient as Client

        async with Client(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as no_origin_client:
            response = await no_origin_client.post(
                f"{API}/auth/signup", json=CREDENTIALS
            )

        assert response.status_code == 201

    async def test_blocks_a_blank_origin(self, client: AsyncClient) -> None:
        """A present-but-empty Origin is not the same as an absent one, and
        it matches no allowed origin, so it is rejected."""
        response = await client.post(
            f"{API}/auth/signup", json=CREDENTIALS, headers={"Origin": ""}
        )

        assert response.status_code == 403

    async def test_blocks_the_null_origin(self, client: AsyncClient) -> None:
        """SECURITY: browsers send `Origin: null` from sandboxed iframes and
        some redirect chains -- exactly the contexts an attacker can arrange,
        so it must not be treated as trusted."""
        response = await client.post(
            f"{API}/auth/signup", json=CREDENTIALS, headers={"Origin": "null"}
        )

        assert response.status_code == 403

    async def test_does_not_block_safe_methods(self, client: AsyncClient) -> None:
        """GET must stay callable from anywhere -- which is also why no
        endpoint may change state on a GET."""
        response = await client.get(
            f"{API}/health", headers={"Origin": "https://evil.example"}
        )

        assert response.status_code == 200

    async def test_blocks_before_credentials_are_checked(
        self, client: AsyncClient
    ) -> None:
        """A rejected origin must not even reach the login logic, so a
        cross-site attacker learns nothing about whether the account exists.
        """
        await client.post(
            f"{API}/auth/signup",
            json=CREDENTIALS,
            headers={"Origin": "http://localhost:5173"},
        )

        response = await client.post(
            f"{API}/auth/login",
            json={"email": CREDENTIALS["email"], "password": "wrong-password-here"},
            headers={"Origin": "https://evil.example"},
        )

        # 403 (origin rejected), not 401 (bad credentials).
        assert response.status_code == 403
