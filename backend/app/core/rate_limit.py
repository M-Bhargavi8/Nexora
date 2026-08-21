"""In-memory rate limiting for authentication endpoints.

**Known limitation, stated plainly:** this limiter lives in the memory of one
process. Run several uvicorn workers and each keeps its own counter, so the
real limit becomes workers x limit; restart the process and all counters
reset. That is acceptable for development and for a single-process
deployment, and it is genuinely better than no limit at all -- but it is not
a distributed rate limiter.

A correct multi-process implementation needs shared state, which is what
Redis is for. That arrives in a later phase, at which point only the storage
inside this module changes; the interface stays the same.

**Why two dimensions.** Limiting by IP alone stops one machine guessing many
passwords, but not a botnet guessing one password from a thousand addresses.
Limiting by account alone stops the botnet, but lets one machine spray a
thousand accounts with the season's most popular password. Login is therefore
throttled on both, and a request is blocked if *either* limit is exceeded.

**Why failures rather than requests.** Counting every request means a user who
signs in successfully still burns quota -- and behind an office NAT, where
hundreds of people share one address, that locks out innocent people. Only
failed attempts count against the login limit, which is also the only thing an
attacker actually generates.
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, Request, status

from app.core.logging import get_logger

logger = get_logger(__name__)

# How often the idle-key sweep is allowed to run. Predictable and frequent:
# the previous hourly sweep meant a key-rotating attacker could grow the
# dictionary unchecked for up to an hour, which is the very thing the sweep
# exists to prevent.
_CLEANUP_INTERVAL_SECONDS = 60

# Absolute ceiling on tracked keys. The sweep alone is not enough -- it is
# time-based, and a fast enough flood outruns it. This bounds memory no matter
# what arrives.
_MAX_TRACKED_KEYS = 10_000


class SlidingWindowRateLimiter:
    """Allow at most `max_attempts` recorded events per `window_seconds` per key.

    A *sliding* window rather than a fixed one: with fixed windows an
    attacker can send the full quota at 11:59:59 and again at 12:00:00,
    getting double the intended rate at the boundary. Here we keep the
    timestamps of recent attempts and discard those that have aged out.

    Checking and recording are separate operations (`check` and `record`)
    because the caller often cannot tell whether an attempt *counts* until
    after it has run -- a login is only a failed attempt once the password has
    been found wrong. `allow` combines the two for callers that do not need
    the distinction.
    """

    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        max_tracked_keys: int = _MAX_TRACKED_KEYS,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._max_tracked_keys = max_tracked_keys
        # A plain dict, not a defaultdict: merely *asking* about a key must
        # not create an entry for it, or a read-only check becomes a memory
        # leak an attacker can drive.
        self._attempts: dict[str, deque[float]] = {}
        self._last_cleanup = time.monotonic()
        # The endpoints are async and never await inside these methods, so the
        # event loop cannot interleave two calls. The lock makes that
        # invariant explicit and keeps the class correct if it is ever touched
        # from a worker thread.
        self._lock = Lock()

    # -- queries ---------------------------------------------------------

    def check(self, key: str) -> bool:
        """Whether `key` is currently under its limit. Records nothing."""
        with self._lock:
            # monotonic(), not time(): it cannot jump backwards when the
            # system clock is adjusted or during a daylight-saving change.
            return self._is_allowed(key, time.monotonic())

    @property
    def tracked_keys(self) -> int:
        """How many keys are currently held. Used by tests and diagnostics."""
        with self._lock:
            return len(self._attempts)

    # -- mutations -------------------------------------------------------

    def record(self, key: str) -> None:
        """Count one attempt against `key`."""
        with self._lock:
            now = time.monotonic()
            self._sweep(now)
            self._append(key, now)

    def allow(self, key: str) -> bool:
        """Record an attempt for `key` and report whether it is permitted.

        Nothing is recorded when the answer is False, so a blocked caller
        cannot extend their own lockout by hammering the endpoint.
        """
        with self._lock:
            now = time.monotonic()
            self._sweep(now)
            if not self._is_allowed(key, now):
                return False
            self._append(key, now)
            return True

    def reset(self, key: str) -> None:
        """Clear a key's history, e.g. after a successful login."""
        with self._lock:
            self._attempts.pop(key, None)

    def clear(self) -> None:
        """Forget every key. Used by tests to isolate one case from the next."""
        with self._lock:
            self._attempts.clear()

    # -- internals (callers already hold the lock) ------------------------

    def _is_allowed(self, key: str, now: float) -> bool:
        stamps = self._attempts.get(key)
        if stamps is None:
            return True
        self._prune(stamps, now)
        return len(stamps) < self.max_attempts

    def _append(self, key: str, now: float) -> None:
        stamps = self._attempts.get(key)
        if stamps is None:
            stamps = self._attempts[key] = deque()
        self._prune(stamps, now)
        stamps.append(now)
        self._enforce_capacity(now)

    def _prune(self, stamps: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while stamps and stamps[0] <= cutoff:
            stamps.popleft()

    def _sweep(self, now: float) -> None:
        """Drop keys whose attempts have all aged out of the window."""
        if now - self._last_cleanup < _CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup = now
        self._drop_expired(now)

    def _drop_expired(self, now: float) -> int:
        cutoff = now - self.window_seconds
        stale = [
            key
            for key, stamps in self._attempts.items()
            if not stamps or stamps[-1] <= cutoff
        ]
        for key in stale:
            del self._attempts[key]
        return len(stale)

    def _enforce_capacity(self, now: float) -> None:
        """Hard ceiling on how many keys we will ever hold."""
        if len(self._attempts) <= self._max_tracked_keys:
            return

        # Expired entries first: they cannot affect any decision.
        self._drop_expired(now)
        if len(self._attempts) <= self._max_tracked_keys:
            return

        # Still over. Evict least-recently-active keys.
        #
        # SECURITY: oldest-first is deliberate, not arbitrary. If eviction
        # picked randomly, an attacker who had used up their quota could flood
        # the limiter with junk keys until their own entry happened to be
        # evicted, clearing their limit on demand. Their entry is by
        # definition the most recent, so oldest-first never chooses it.
        overflow = len(self._attempts) - self._max_tracked_keys
        oldest = sorted(
            self._attempts,
            key=lambda key: self._attempts[key][-1] if self._attempts[key] else 0.0,
        )[:overflow]
        for key in oldest:
            del self._attempts[key]

        logger.warning(
            "Rate limiter at capacity; evicted least-recent keys",
            extra={"evicted": overflow, "capacity": self._max_tracked_keys},
        )


def client_key(request: Request) -> str:
    """Identify the caller for rate-limiting purposes.

    Uses the peer address. Behind a reverse proxy every request appears to
    come from the proxy, so production will need to read `X-Forwarded-For`
    -- but only when the proxy is trusted, because that header is trivially
    forged by the client otherwise. Trusting it blindly would let an attacker
    bypass the limit entirely by sending a random value each request. Wiring
    that up belongs with the deployment work in the production phase.
    """
    if request.client is None:
        return "unknown"
    return request.client.host


def _too_many_attempts(retry_after_seconds: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Please wait a moment and try again.",
        headers={"Retry-After": str(retry_after_seconds)},
    )


class RateLimit:
    """FastAPI dependency that enforces a limiter on an endpoint.

    Counts every request that reaches it, which is what endpoints such as
    signup want: there, a *successful* call is itself the thing worth
    limiting, because mass account creation is the abuse.
    """

    def __init__(self, limiter: SlidingWindowRateLimiter, name: str) -> None:
        self._limiter = limiter
        self._name = name

    async def __call__(self, request: Request) -> None:
        key = f"{self._name}:{client_key(request)}"
        if not self._limiter.allow(key):
            logger.warning(
                "Rate limit exceeded",
                extra={"limit_name": self._name, "path": request.url.path},
            )
            raise _too_many_attempts(self._limiter.window_seconds)


class AuthThrottle:
    """Two-dimensional throttle for credential checking.

    Used explicitly inside the login handler rather than as a dependency,
    because whether an attempt counts is only known *after* the password has
    been checked -- and a dependency runs before the handler.
    """

    def __init__(
        self,
        ip_limiter: SlidingWindowRateLimiter,
        account_limiter: SlidingWindowRateLimiter,
        name: str,
    ) -> None:
        self._ip = ip_limiter
        self._account = account_limiter
        self._name = name

    def _ip_key(self, request: Request) -> str:
        return f"{self._name}:ip:{client_key(request)}"

    def _account_key(self, account: str) -> str:
        # Normalised here as well as by the caller. Two spellings of one
        # address must not get two separate quotas, or the limit multiplies by
        # however many variations an attacker can type.
        return f"{self._name}:account:{account.strip().lower()}"

    def check(self, request: Request, account: str) -> None:
        """Raise 429 if either dimension is already over its limit.

        Records nothing: this runs *before* the attempt, and an attempt that
        never happened must not count against anyone.
        """
        if not self._ip.check(self._ip_key(request)):
            logger.warning(
                "Rate limit exceeded",
                extra={"limit_name": f"{self._name}:ip", "path": request.url.path},
            )
            raise _too_many_attempts(self._ip.window_seconds)

        if not self._account.check(self._account_key(account)):
            logger.warning(
                "Rate limit exceeded",
                extra={"limit_name": f"{self._name}:account", "path": request.url.path},
            )
            raise _too_many_attempts(self._account.window_seconds)

    def record_failure(self, request: Request, account: str) -> None:
        """Count one failed credential check against both dimensions."""
        self._ip.record(self._ip_key(request))
        self._account.record(self._account_key(account))

    def record_success(self, request: Request, account: str) -> None:
        """Clear the account's failure history after a genuine sign-in.

        SECURITY: this deliberately does **not** reset the IP counter. An
        attacker who controls one valid account could otherwise guess nine
        times, sign in to their own account to wipe the IP counter, and repeat
        indefinitely -- turning the per-IP limit into no limit at all. The IP
        history is left to age out of the sliding window on its own.
        """
        self._account.reset(self._account_key(account))
