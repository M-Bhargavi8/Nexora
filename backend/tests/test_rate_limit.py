"""Tests for the sliding-window rate limiter and its use on auth endpoints."""

from __future__ import annotations

import time

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.auth import reset_auth_rate_limits
from app.core.rate_limit import SlidingWindowRateLimiter

API = "/api/v1"
PASSWORD = "a-sufficiently-long-password"
WRONG = "wrong-password-attempt"


def client_from_ip(app: FastAPI, host: str) -> AsyncClient:
    """A client that appears to come from a specific address.

    The per-IP limit is only meaningful if two different callers really are
    treated as two different callers, and the default test client always looks
    like the same one.
    """
    return AsyncClient(
        transport=ASGITransport(app=app, client=(host, 50000)),
        base_url="http://testserver",
        headers={"Origin": "http://localhost:5173"},
    )


async def failed_login(client: AsyncClient, email: str) -> int:
    response = await client.post(
        f"{API}/auth/login", json={"email": email, "password": WRONG}
    )
    return response.status_code


class TestSlidingWindowRateLimiter:
    def test_allows_up_to_the_limit(self) -> None:
        limiter = SlidingWindowRateLimiter(max_attempts=3, window_seconds=60)

        assert [limiter.allow("k") for _ in range(3)] == [True, True, True]

    def test_blocks_beyond_the_limit(self) -> None:
        limiter = SlidingWindowRateLimiter(max_attempts=3, window_seconds=60)
        for _ in range(3):
            limiter.allow("k")

        assert limiter.allow("k") is False

    def test_tracks_keys_independently(self) -> None:
        """One user hitting the limit must not lock out everyone else."""
        limiter = SlidingWindowRateLimiter(max_attempts=2, window_seconds=60)
        limiter.allow("attacker")
        limiter.allow("attacker")

        assert limiter.allow("attacker") is False
        assert limiter.allow("innocent-bystander") is True

    def test_allows_again_once_the_window_passes(self) -> None:
        limiter = SlidingWindowRateLimiter(max_attempts=2, window_seconds=1)
        limiter.allow("k")
        limiter.allow("k")
        assert limiter.allow("k") is False

        time.sleep(1.05)

        assert limiter.allow("k") is True

    def test_reset_clears_a_single_key(self) -> None:
        limiter = SlidingWindowRateLimiter(max_attempts=1, window_seconds=60)
        limiter.allow("k")
        assert limiter.allow("k") is False

        limiter.reset("k")

        assert limiter.allow("k") is True


class TestLimiterDoesNotLeakMemory:
    """A caller rotating keys must not be able to grow the store forever."""

    def test_check_does_not_record_an_attempt(self) -> None:
        limiter = SlidingWindowRateLimiter(max_attempts=2, window_seconds=60)

        for _ in range(50):
            assert limiter.check("k") is True

        # None of those checks consumed quota.
        assert limiter.allow("k") is True
        assert limiter.allow("k") is True
        assert limiter.allow("k") is False

    def test_check_does_not_create_a_key(self) -> None:
        """A read-only question must not allocate storage, or merely asking
        about a million addresses becomes the denial of service."""
        limiter = SlidingWindowRateLimiter(max_attempts=2, window_seconds=60)

        for i in range(1000):
            limiter.check(f"key-{i}")

        assert limiter.tracked_keys == 0

    def test_tracked_keys_are_capped(self) -> None:
        limiter = SlidingWindowRateLimiter(
            max_attempts=5, window_seconds=60, max_tracked_keys=10
        )

        for i in range(500):
            limiter.record(f"key-{i}")

        assert limiter.tracked_keys <= 10

    def test_memory_stays_bounded_under_a_key_flood(self) -> None:
        """SECURITY: eviction is oldest-first. Were it arbitrary, an attacker
        who had used up their quota could flood the limiter until their own
        entry was dropped, clearing their limit on demand."""
        limiter = SlidingWindowRateLimiter(
            max_attempts=1, window_seconds=60, max_tracked_keys=10
        )
        limiter.record("attacker")
        assert limiter.check("attacker") is False

        for i in range(500):
            limiter.record(f"filler-{i}")

        assert limiter.tracked_keys <= 10


class TestAuthEndpointRateLimiting:
    async def test_repeated_failed_logins_are_eventually_blocked(
        self, client: AsyncClient
    ) -> None:
        """SECURITY: without this, an attacker can try passwords as fast as
        the network allows."""
        payload = {"email": "ada@example.com", "password": "wrong-password-attempt"}

        statuses = []
        for _ in range(12):
            response = await client.post(f"{API}/auth/login", json=payload)
            statuses.append(response.status_code)

        assert 429 in statuses, f"never rate limited: {statuses}"

    async def test_the_block_response_tells_the_client_when_to_retry(
        self, client: AsyncClient
    ) -> None:
        payload = {"email": "ada@example.com", "password": "wrong-password-attempt"}

        response = None
        for _ in range(12):
            response = await client.post(f"{API}/auth/login", json=payload)
            if response.status_code == 429:
                break

        assert response is not None
        assert response.status_code == 429
        assert "retry-after" in {k.lower() for k in response.headers}

    async def test_health_endpoints_are_not_rate_limited(
        self, client: AsyncClient
    ) -> None:
        """The limiter is scoped to authentication. Rate-limiting liveness
        would make an orchestrator conclude a busy service was dead."""
        statuses = [
            (await client.get(f"{API}/health")).status_code for _ in range(25)
        ]

        assert set(statuses) == {200}


class TestSuccessfulLoginResetsTheAccountCounter:
    async def test_a_successful_login_clears_that_accounts_failures(
        self, client: AsyncClient
    ) -> None:
        """A user who mistypes a few times and then succeeds must not carry a
        penalty into their next session."""
        await client.post(
            f"{API}/auth/signup",
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        reset_auth_rate_limits()

        # Four failures -- one short of the per-account limit of five.
        for _ in range(4):
            assert await failed_login(client, "ada@example.com") == 401

        good = await client.post(
            f"{API}/auth/login",
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        assert good.status_code == 200

        # Four more must still be possible. Without the reset, the fifth
        # failure overall would already have been blocked.
        statuses = [await failed_login(client, "ada@example.com") for _ in range(4)]
        assert statuses == [401, 401, 401, 401], statuses

    async def test_a_successful_login_does_not_clear_the_ip_counter(
        self, client: AsyncClient
    ) -> None:
        """SECURITY: the bypass this guards against is an attacker who owns one
        valid account guessing nine times, signing in to their own account to
        wipe the IP counter, and repeating indefinitely."""
        await client.post(
            f"{API}/auth/signup",
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        reset_auth_rate_limits()

        # Nine failures spread over nine accounts, so no single account limit
        # is reached and only the per-IP counter accumulates.
        for i in range(9):
            assert await failed_login(client, f"user{i}@example.com") == 401

        good = await client.post(
            f"{API}/auth/login",
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        assert good.status_code == 200

        # The tenth failure fills the per-IP quota; the eleventh is blocked.
        assert await failed_login(client, "user9@example.com") == 401
        assert await failed_login(client, "user10@example.com") == 429


class TestAccountDimension:
    async def test_email_spelling_cannot_multiply_the_quota(
        self, client: AsyncClient
    ) -> None:
        """SECURITY: differing case must not buy a fresh bucket. The per-IP
        limit is 10, so a block after only five attempts can only have come
        from the account dimension."""
        variants = [
            "ada@example.com",
            "ADA@example.com",
            "Ada@Example.Com",
            "aDa@ExAmPlE.cOm",
            "ADA@EXAMPLE.COM",
        ]
        for email in variants:
            assert await failed_login(client, email) == 401

        assert await failed_login(client, "ada@example.com") == 429

    async def test_one_account_being_attacked_does_not_lock_out_others(
        self, client: AsyncClient
    ) -> None:
        for _ in range(5):
            await failed_login(client, "victim@example.com")
        assert await failed_login(client, "victim@example.com") == 429

        assert await failed_login(client, "bystander@example.com") == 401


class TestIpDimension:
    async def test_one_address_being_blocked_does_not_affect_another(
        self, app: FastAPI
    ) -> None:
        async with client_from_ip(app, "10.0.0.1") as attacker:
            for i in range(10):
                await failed_login(attacker, f"user{i}@example.com")
            assert await failed_login(attacker, "another@example.com") == 429

        async with client_from_ip(app, "10.0.0.2") as innocent:
            assert await failed_login(innocent, "someone@example.com") == 401


class TestSignupLimiting:
    async def test_signup_counts_successful_attempts_too(
        self, client: AsyncClient
    ) -> None:
        """Unlike login, the abuse here is mass account creation, so a
        *successful* signup is exactly what needs limiting."""
        statuses = []
        for i in range(12):
            response = await client.post(
                f"{API}/auth/signup",
                json={"email": f"new{i}@example.com", "password": PASSWORD},
            )
            statuses.append(response.status_code)

        assert 429 in statuses, statuses
