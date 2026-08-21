"""Tests for the authentication service layer.

These call the service directly, which is the only practical way to reach
behaviour the HTTP surface hides -- notably session expiry, which would
otherwise need a test that waits seven days.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.security import hash_session_token
from app.models.session import UserSession
from app.models.user import User
from app.services.auth import (
    EmailAlreadyRegistered,
    authenticate_user,
    create_session,
    create_user,
    get_user_for_session_token,
    normalize_email,
    revoke_session,
)

EMAIL = "ada@example.com"
PASSWORD = "a-sufficiently-long-password"


class TestNormalizeEmail:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ADA@EXAMPLE.COM", "ada@example.com"),
            ("  ada@example.com  ", "ada@example.com"),
            ("Ada@Example.Com", "ada@example.com"),
            ("ada@example.com", "ada@example.com"),
        ],
    )
    def test_normalises(self, raw: str, expected: str) -> None:
        assert normalize_email(raw) == expected


class TestCreateUser:
    async def test_stores_a_hash_not_the_password(self, db: AsyncSession) -> None:
        user = await create_user(db, email=EMAIL, password=PASSWORD)

        assert user.password_hash != PASSWORD
        assert user.password_hash.startswith("$argon2id$")

    async def test_rejects_a_duplicate(self, db: AsyncSession) -> None:
        await create_user(db, email=EMAIL, password=PASSWORD)

        with pytest.raises(EmailAlreadyRegistered):
            await create_user(db, email=EMAIL, password=PASSWORD)


class TestAuthenticateUser:
    async def test_returns_the_user_for_valid_credentials(
        self, db: AsyncSession
    ) -> None:
        created = await create_user(db, email=EMAIL, password=PASSWORD)

        authenticated = await authenticate_user(db, email=EMAIL, password=PASSWORD)

        assert authenticated is not None
        assert authenticated.id == created.id

    async def test_returns_none_for_a_wrong_password(self, db: AsyncSession) -> None:
        await create_user(db, email=EMAIL, password=PASSWORD)

        assert await authenticate_user(db, email=EMAIL, password="wrong-password") is None

    async def test_returns_none_for_an_unknown_email(self, db: AsyncSession) -> None:
        assert await authenticate_user(db, email="nobody@example.com", password=PASSWORD) is None

    async def test_refuses_a_deactivated_account(self, db: AsyncSession) -> None:
        """SECURITY: disabling an account must actually prevent login, even
        though the password is still correct."""
        user = await create_user(db, email=EMAIL, password=PASSWORD)
        user.is_active = False
        await db.commit()

        assert await authenticate_user(db, email=EMAIL, password=PASSWORD) is None


class TestSessions:
    async def test_stores_only_the_token_hash(self, db: AsyncSession) -> None:
        """SECURITY: a database dump must contain no usable session tokens."""
        user = await create_user(db, email=EMAIL, password=PASSWORD)

        token = await create_session(db, user)

        stored = (await db.execute(select(UserSession))).scalar_one()
        assert stored.token_hash != token
        assert stored.token_hash == hash_session_token(token)

    async def test_resolves_a_valid_token_to_its_user(self, db: AsyncSession) -> None:
        user = await create_user(db, email=EMAIL, password=PASSWORD)
        token = await create_session(db, user)

        resolved = await get_user_for_session_token(db, token)

        assert resolved is not None
        assert resolved.id == user.id

    async def test_rejects_an_unknown_token(self, db: AsyncSession) -> None:
        assert await get_user_for_session_token(db, "never-issued") is None

    async def test_rejects_an_expired_session(self, db: AsyncSession) -> None:
        """Reached by backdating the expiry, since waiting out the real
        seven-day lifetime is not a viable test."""
        user = await create_user(db, email=EMAIL, password=PASSWORD)
        token = await create_session(db, user)

        session = (await db.execute(select(UserSession))).scalar_one()
        session.expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

        assert await get_user_for_session_token(db, token) is None

    async def test_removes_the_expired_row(self, db: AsyncSession) -> None:
        """Expired sessions are cleaned up on access so the table does not
        grow forever."""
        user = await create_user(db, email=EMAIL, password=PASSWORD)
        token = await create_session(db, user)
        session = (await db.execute(select(UserSession))).scalar_one()
        session.expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

        await get_user_for_session_token(db, token)

        assert (await db.execute(select(UserSession))).scalar_one_or_none() is None

    async def test_a_session_stops_working_when_its_user_is_deactivated(
        self, db: AsyncSession
    ) -> None:
        """SECURITY: disabling an account must end existing logins, not just
        block new ones."""
        user = await create_user(db, email=EMAIL, password=PASSWORD)
        token = await create_session(db, user)

        user.is_active = False
        await db.commit()

        assert await get_user_for_session_token(db, token) is None

    async def test_revoke_removes_the_session(self, db: AsyncSession) -> None:
        user = await create_user(db, email=EMAIL, password=PASSWORD)
        token = await create_session(db, user)

        assert await revoke_session(db, token) is True
        assert await get_user_for_session_token(db, token) is None

    async def test_revoking_an_unknown_token_reports_no_change(
        self, db: AsyncSession
    ) -> None:
        assert await revoke_session(db, "never-issued") is False

    async def test_deleting_a_user_cascades_to_their_sessions(
        self, db: AsyncSession
    ) -> None:
        """The foreign key is ON DELETE CASCADE, so no session can outlive
        its user and authenticate as a deleted account."""
        user = await create_user(db, email=EMAIL, password=PASSWORD)
        await create_session(db, user)

        await db.delete(await db.get(User, user.id))
        await db.commit()

        assert (await db.execute(select(UserSession))).scalar_one_or_none() is None
