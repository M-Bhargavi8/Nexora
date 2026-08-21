"""The login-session model.

Named ``UserSession`` rather than ``Session`` on purpose: SQLAlchemy's own
database session is also called ``Session``, and having two different things
with the same name in one codebase is a reliable source of confusing bugs.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import ensure_utc, utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User

# A SHA-256 digest rendered as hex is always exactly 64 characters.
TOKEN_HASH_LENGTH = 64


class UserSession(Base):
    """One active login.

    The token handed to the browser is **not** stored here -- only its
    SHA-256 hash. If this table leaked, the hashes could not be used to
    authenticate, because the server hashes the incoming cookie value and
    compares hashes.

    Because sessions are rows, logging out is a real operation: delete the
    row and the credential stops working immediately. That is the property a
    plain signed token cannot give you.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        # ondelete="CASCADE" is enforced by the database itself, so sessions
        # cannot outlive their user even if a row is deleted by a script that
        # bypasses the ORM.
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Unique + indexed: every authenticated request looks a session up by
    # this column, so it must be fast, and two sessions can never collide.
    token_hash: Mapped[str] = mapped_column(
        String(TOKEN_HASH_LENGTH), unique=True, index=True, nullable=False
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")

    @property
    def is_expired(self) -> bool:
        """Whether this session has passed its expiry time.

        `ensure_utc` is applied because SQLite returns naive datetimes even
        for timezone-aware columns; comparing naive with aware raises
        TypeError.
        """
        return ensure_utc(self.expires_at) <= utcnow()

    def __repr__(self) -> str:
        # Never includes token_hash.
        return f"<UserSession id={self.id} user_id={self.user_id}>"
