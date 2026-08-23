"""A chat conversation.

This is the first user-*owned* resource in NEXORA. Users and sessions describe
who someone is; a conversation is the first thing one person has and another
person must not be able to read. Every query for a conversation therefore
filters on `user_id` -- see `app/services/chat.py`. Filtering by id alone
would be an "insecure direct object reference": guess or increment an
identifier and read someone else's chat. UUID primary keys make guessing
impractical, but they are obfuscation, not authorization. The filter is the
authorization.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.message import Message
    from app.models.user import User

TITLE_MAX_LENGTH = 200


class Conversation(Base):
    """One chat thread belonging to exactly one user."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        # Enforced by the database, so deleting an account really does remove
        # its conversations -- and their messages, which cascade in turn.
        ForeignKey("users.id", ondelete="CASCADE"),
        # Indexed because "list my conversations" is the single most frequent
        # query this table will ever serve.
        index=True,
        nullable=False,
    )

    # Nullable on purpose: NULL means "not titled yet", which is honestly
    # different from a title that happens to be empty.
    title: Mapped[str | None] = mapped_column(String(TITLE_MAX_LENGTH), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    # Sorting the sidebar by recency means "most recently *messaged*", not
    # "most recently edited", so this is bumped explicitly by `append_message`
    # and by nothing else.
    #
    # There is deliberately no `onupdate=utcnow` here. It would fire on *any*
    # write to the row -- including a rename -- and send a conversation you
    # merely retitled straight to the top of the sidebar as though you had just
    # used it. Renaming is not activity. Note that `onupdate` is a Python-side
    # default rather than DDL, so removing it needs no migration.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="conversations")

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        # Ordered by the explicit counter, not by timestamp -- see the note
        # on Message.position for why timestamps tie and produce answers above
        # their questions.
        order_by="Message.position",
    )

    def __repr__(self) -> str:
        # Excludes the title, which is derived from user content.
        return f"<Conversation id={self.id} user_id={self.user_id}>"
