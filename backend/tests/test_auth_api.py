"""End-to-end tests for the authentication endpoints.

These drive the real HTTP surface -- request in, response and cookies out --
so they cover routing, validation, the service layer, and the database
together. This is the suite that proves authentication actually works.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API = "/api/v1"
EMAIL = "ada@example.com"
PASSWORD = "a-sufficiently-long-password"


async def signup(client: AsyncClient, email: str = EMAIL, password: str = PASSWORD):
    return await client.post(
        f"{API}/auth/signup", json={"email": email, "password": password}
    )


async def login(client: AsyncClient, email: str = EMAIL, password: str = PASSWORD):
    return await client.post(
        f"{API}/auth/login", json={"email": email, "password": password}
    )


class TestSignup:
    async def test_creates_an_account(self, client: AsyncClient) -> None:
        response = await signup(client)

        assert response.status_code == 201
        body = response.json()
        assert body["email"] == EMAIL
        assert body["is_active"] is True
        assert "id" in body

    async def test_never_returns_the_password_or_its_hash(
        self, client: AsyncClient
    ) -> None:
        """The response schema excludes password_hash. If someone later adds
        the field to the model, this test is what stops it reaching a
        response body."""
        response = await signup(client)

        raw = response.text.lower()
        assert "password" not in raw
        assert "argon2" not in raw

    async def test_signs_the_new_user_in(self, client: AsyncClient) -> None:
        await signup(client)

        me = await client.get(f"{API}/auth/me")

        assert me.status_code == 200
        assert me.json()["email"] == EMAIL

    async def test_rejects_a_duplicate_email(self, client: AsyncClient) -> None:
        await signup(client)

        response = await signup(client)

        assert response.status_code == 409

    async def test_rejects_a_duplicate_email_in_a_different_case(
        self, client: AsyncClient
    ) -> None:
        """SECURITY: emails are normalised before the uniqueness check.
        Without this, `Ada@Example.com` creates a second account for the same
        person, and either account could then be used to reset the other."""
        await signup(client, email="ada@example.com")

        response = await signup(client, email="ADA@Example.COM")

        assert response.status_code == 409

    async def test_stores_the_email_lowercased(self, client: AsyncClient) -> None:
        response = await signup(client, email="  ADA@Example.COM  ")

        assert response.json()["email"] == "ada@example.com"

    @pytest.mark.parametrize(
        "password",
        ["short", "elevenchar", "", "a" * 11],
    )
    async def test_rejects_a_password_below_the_minimum_length(
        self, client: AsyncClient, password: str
    ) -> None:
        response = await signup(client, password=password)

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "password",
        # All are >= 12 characters, so they pass the length rule and are
        # rejected by the blocklist specifically -- not incidentally by
        # min_length, which would make this test prove nothing.
        ["password1234", "PASSWORD1234", "123456789012", "administrator"],
    )
    async def test_rejects_a_common_password(
        self, client: AsyncClient, password: str
    ) -> None:
        response = await signup(client, password=password)

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "password", ["aaaaaaaaaaaaaa", "abababababababab", "123412341234"]
    )
    async def test_rejects_a_password_with_too_few_distinct_characters(
        self, client: AsyncClient, password: str
    ) -> None:
        """Long but nearly entropy-free. One general rule beats enumerating
        every repetitive variation."""
        response = await signup(client, password=password)

        assert response.status_code == 422

    @pytest.mark.parametrize("email", ["not-an-email", "@example.com", "ada@", ""])
    async def test_rejects_a_malformed_email(
        self, client: AsyncClient, email: str
    ) -> None:
        response = await signup(client, email=email)

        assert response.status_code == 422

    async def test_rejects_an_absurdly_long_password(
        self, client: AsyncClient
    ) -> None:
        """SECURITY: unbounded input is a denial-of-service vector, because
        hashing cost grows with length."""
        response = await signup(client, password="x" * 5000)

        assert response.status_code == 422


class TestSessionCookie:
    async def test_cookie_is_set_on_signup(self, client: AsyncClient) -> None:
        response = await signup(client)

        assert "nexora_session" in response.cookies

    async def test_cookie_is_httponly(self, client: AsyncClient) -> None:
        """SECURITY: the flag that stops an XSS bug from stealing the
        session."""
        response = await signup(client)

        assert "httponly" in response.headers["set-cookie"].lower()

    async def test_cookie_is_samesite_lax(self, client: AsyncClient) -> None:
        """SECURITY: the primary CSRF defence."""
        response = await signup(client)

        assert "samesite=lax" in response.headers["set-cookie"].lower()

    async def test_cookie_value_is_not_the_stored_hash(
        self, client: AsyncClient
    ) -> None:
        """The browser holds the raw token; the database holds its SHA-256
        hash. A 64-character hex string in the cookie would mean we had
        stored and sent the same value."""
        response = await signup(client)
        token = response.cookies["nexora_session"]

        assert len(token) != 64 or not all(c in "0123456789abcdef" for c in token)


class TestLogin:
    async def test_succeeds_with_correct_credentials(
        self, client: AsyncClient
    ) -> None:
        await signup(client)
        await client.post(f"{API}/auth/logout")

        response = await login(client)

        assert response.status_code == 200
        assert response.json()["email"] == EMAIL

    async def test_is_case_insensitive_for_the_email(
        self, client: AsyncClient
    ) -> None:
        await signup(client)
        await client.post(f"{API}/auth/logout")

        response = await login(client, email="ADA@EXAMPLE.COM")

        assert response.status_code == 200

    async def test_rejects_a_wrong_password(self, client: AsyncClient) -> None:
        await signup(client)

        response = await login(client, password="definitely-not-the-password")

        assert response.status_code == 401

    async def test_rejects_an_unknown_email(self, client: AsyncClient) -> None:
        response = await login(client, email="nobody@example.com")

        assert response.status_code == 401

    async def test_gives_an_identical_error_for_unknown_email_and_wrong_password(
        self, client: AsyncClient
    ) -> None:
        """SECURITY: differing messages turn login into an oracle that tells
        an attacker which email addresses are registered."""
        await signup(client)

        wrong_password = await login(client, password="definitely-not-the-password")
        unknown_email = await login(client, email="nobody@example.com")

        assert wrong_password.status_code == unknown_email.status_code
        assert wrong_password.json() == unknown_email.json()

    async def test_password_is_case_sensitive(self, client: AsyncClient) -> None:
        await signup(client)

        response = await login(client, password=PASSWORD.upper())

        assert response.status_code == 401


class TestCurrentUser:
    async def test_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.get(f"{API}/auth/me")

        assert response.status_code == 401

    async def test_rejects_a_forged_cookie(self, client: AsyncClient) -> None:
        """SECURITY: the token is looked up in the database, so a made-up
        value cannot authenticate no matter how well-formed it looks."""
        client.cookies.set("nexora_session", "a-completely-made-up-token-value")

        response = await client.get(f"{API}/auth/me")

        assert response.status_code == 401

    async def test_returns_the_signed_in_user(self, client: AsyncClient) -> None:
        await signup(client)

        response = await client.get(f"{API}/auth/me")

        assert response.status_code == 200
        assert response.json()["email"] == EMAIL


class TestLogout:
    async def test_reports_success(self, client: AsyncClient) -> None:
        await signup(client)

        response = await client.post(f"{API}/auth/logout")

        assert response.status_code == 200

    async def test_session_stops_working_afterwards(
        self, client: AsyncClient
    ) -> None:
        """This is the property a plain signed token cannot provide: the
        credential is dead immediately, not when it eventually expires."""
        await signup(client)
        assert (await client.get(f"{API}/auth/me")).status_code == 200

        await client.post(f"{API}/auth/logout")

        assert (await client.get(f"{API}/auth/me")).status_code == 401

    async def test_a_revoked_token_cannot_be_replayed(
        self, client: AsyncClient
    ) -> None:
        """Even if an attacker captured the cookie value before logout, it is
        useless afterwards because the database row is gone."""
        signup_response = await signup(client)
        stolen_token = signup_response.cookies["nexora_session"]

        await client.post(f"{API}/auth/logout")
        client.cookies.set("nexora_session", stolen_token)

        assert (await client.get(f"{API}/auth/me")).status_code == 401

    async def test_succeeds_without_a_session(self, client: AsyncClient) -> None:
        """Someone holding a stale cookie must be able to clear it rather
        than being stuck with a 401 they cannot resolve."""
        response = await client.post(f"{API}/auth/logout")

        assert response.status_code == 200


class TestUserIsolation:
    async def test_each_session_resolves_to_its_own_user(
        self, app, session_factory
    ) -> None:
        """Two independent clients must never see each other's identity.

        Separate AsyncClient instances are used so each has its own cookie
        jar, exactly like two different browsers.
        """
        from httpx import ASGITransport, AsyncClient as Client

        transport = ASGITransport(app=app)
        headers = {"Origin": "http://localhost:5173"}

        async with Client(
            transport=transport, base_url="http://testserver", headers=headers
        ) as ada, Client(
            transport=transport, base_url="http://testserver", headers=headers
        ) as grace:
            await signup(ada, email="ada@example.com")
            await signup(grace, email="grace@example.com")

            ada_me = await ada.get(f"{API}/auth/me")
            grace_me = await grace.get(f"{API}/auth/me")

            assert ada_me.json()["email"] == "ada@example.com"
            assert grace_me.json()["email"] == "grace@example.com"
            assert ada_me.json()["id"] != grace_me.json()["id"]

    async def test_logging_out_does_not_affect_another_user(
        self, app
    ) -> None:
        from httpx import ASGITransport, AsyncClient as Client

        transport = ASGITransport(app=app)
        headers = {"Origin": "http://localhost:5173"}

        async with Client(
            transport=transport, base_url="http://testserver", headers=headers
        ) as ada, Client(
            transport=transport, base_url="http://testserver", headers=headers
        ) as grace:
            await signup(ada, email="ada@example.com")
            await signup(grace, email="grace@example.com")

            await ada.post(f"{API}/auth/logout")

            assert (await ada.get(f"{API}/auth/me")).status_code == 401
            assert (await grace.get(f"{API}/auth/me")).status_code == 200
