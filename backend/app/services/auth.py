"""Authentication business logic: accounts, credentials, and sessions."""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    password_needs_rehash,
    verify_password,
)
from app.models.session import UserSession
from app.models.user import User

logger = get_logger(__name__)


class EmailAlreadyRegistered(Exception):
    """Raised when signing up with an email that already has an account."""


# Verifying a password takes ~50ms. Looking up a non-existent user takes
# ~1ms. Without compensation, an attacker can tell registered emails from
# unregistered ones purely by response time -- an "account enumeration
# oracle". We verify against this throwaway hash when no user is found, so
# both paths cost the same.
#
# Computed on first use rather than at import. Hashing at import time made
# *every* `import app` pay ~50ms -- alembic, the test suite, any CLI tool --
# for a value most of them never touch. The defence itself is unchanged: this
# is still a real Argon2 verification against a real hash, and `lru_cache`
# means it is computed exactly once. The application warms it during startup
# (see app/main.py) so the first unknown-email login is no slower than the
# rest -- otherwise that one request would leak by being unusually *long*.
@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    return hash_password("timing-equalisation-placeholder-value")


def warm_timing_defence() -> None:
    """Precompute the hash used to equalise unknown-email login timing."""
    _dummy_password_hash()


def normalize_email(email: str) -> str:
    """Reduce an email to its canonical stored form.

    Done in application code rather than with a PostgreSQL-specific
    case-insensitive column type, so behaviour is identical on every database
    engine and is directly testable.
    """
    return email.strip().lower()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def create_user(db: AsyncSession, email: str, password: str) -> User:
    """Register a new account.

    Raises `EmailAlreadyRegistered` if the address is taken.
    """
    normalized = normalize_email(email)

    # Check first so the common case gives a clean error...
    if await get_user_by_email(db, normalized) is not None:
        raise EmailAlreadyRegistered(normalized)

    user = User(email=normalized, password_hash=hash_password(password))
    db.add(user)

    try:
        await db.commit()
    except IntegrityError:
        # ...but the check above is not enough on its own. Two simultaneous
        # signups can both pass it and then both insert. The unique index is
        # the real guarantee; this converts the resulting database error into
        # the same clean exception.
        await db.rollback()
        raise EmailAlreadyRegistered(normalized) from None

    await db.refresh(user)
    logger.info("Account created", extra={"user_id": str(user.id)})
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    """Return the user if the credentials are valid, otherwise None.

    Returns None -- rather than raising different errors -- for every failure
    mode (unknown email, wrong password, deactivated account) so that the
    caller cannot accidentally reveal which one occurred.
    """
    user = await get_user_by_email(db, email)

    if user is None:
        # Spend the same time as a real verification. See _dummy_password_hash.
        verify_password(password, _dummy_password_hash())
        return None

    if not verify_password(password, user.password_hash):
        return None

    if not user.is_active:
        logger.warning(
            "Login attempt on a deactivated account", extra={"user_id": str(user.id)}
        )
        return None

    # Login is the only moment we hold the plaintext password, so it is the
    # only chance to transparently upgrade a hash created with older, weaker
    # Argon2 parameters.
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        await db.commit()
        logger.info("Password hash upgraded", extra={"user_id": str(user.id)})

    return user


async def create_session(db: AsyncSession, user: User) -> str:
    """Start a login session and return the raw token for the cookie.

    The raw token is returned to the caller and **never stored**; only its
    hash goes in the database. This is the one moment the plaintext token
    exists, so it cannot be recovered later -- exactly like a password.
    """
    settings = get_settings()
    token = generate_session_token()

    session = UserSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        expires_at=utcnow() + timedelta(days=settings.session_lifetime_days),
    )
    db.add(session)
    await db.commit()

    logger.info("Session created", extra={"user_id": str(user.id)})
    return token


async def get_user_for_session_token(db: AsyncSession, token: str) -> User | None:
    """Resolve a raw session token to its user, or None if it is not valid.

    Note the incoming token is hashed and the *hash* is looked up. The
    database never sees a usable token, and the indexed unique column makes
    this a single fast lookup.
    """
    result = await db.execute(
        select(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )
    session = result.scalar_one_or_none()

    if session is None:
        return None

    if session.is_expired:
        # Clean up as we go, so expired rows do not accumulate forever.
        await db.delete(session)
        await db.commit()
        return None

    user = await db.get(User, session.user_id)

    if user is None or not user.is_active:
        return None

    return user


async def revoke_session(db: AsyncSession, token: str) -> bool:
    """Delete one session. Returns whether a session was actually removed.

    This is what makes logout real: the credential stops working the instant
    the row disappears, with no waiting for an expiry time.
    """
    result = await db.execute(
        delete(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )
    await db.commit()
    return (result.rowcount or 0) > 0
