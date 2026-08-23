"""One turn in a conversation.

Note what is deliberately absent: a `user_id` column. A message belongs to a
conversation, and the conversation belongs to a user. Copying `user_id` here
would create two sources of truth for one fact, and the failure mode is ugly:
a message whose `user_id` disagrees with its conversation's is either a
security hole or an invisible bug, depending on which one the query trusts.
Ownership is established once, on the conversation, and every read reaches
messages *through* it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class MessageRole(str, enum.Enum):
    """Who produced a message.

    Inherits from `str` so the value compares equal to its wire form and
    serialises without a custom encoder.

    Only the two roles a conversation currently contains. Phase 7 adds
    tool-call turns; extending this is a one-line migration precisely because
    the column is a VARCHAR with a CHECK constraint rather than a native
    PostgreSQL ENUM -- adding a value to a native enum needs `ALTER TYPE` and
    cannot run inside a transaction on older servers.
    """

    USER = "user"
    ASSISTANT = "assistant"


class Message(Base):
    """A single user or assistant turn."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )

    role: Mapped[MessageRole] = mapped_column(
        Enum(
            MessageRole,
            # VARCHAR + CHECK rather than a native PostgreSQL type: portable to
            # SQLite (which the tests use) and cheap to extend later.
            native_enum=False,
            length=16,
            name="message_role",
            # Left False (the default). The CHECK is declared explicitly in
            # __table_args__ instead -- see the note there.
            create_constraint=False,
            # Without this, SQLAlchemy stores the *member names* ("USER") while
            # the application reads the values ("user"). Everything appears to
            # work until something queries the column directly.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    # Position within the conversation, 0-based. This -- not `created_at` --
    # is the sort key.
    #
    # Timestamps are the obvious choice and the wrong one. Clock resolution is
    # coarse (~15ms on Windows), and a reply can be produced in well under
    # that, so a question and its answer routinely land on the *same*
    # timestamp. Sorting by a tied timestamp with a UUID tiebreaker gives a
    # stable order that is not the chronological one -- which renders the
    # answer above the question. An explicit counter cannot tie.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # Text, not String(n): there is no sensible maximum for a model reply, and
    # a length cap here would truncate answers rather than reject them.
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Cost accounting -------------------------------------------------
    # Populated for assistant messages only; NULL on user turns.
    #
    # These three columns exist now rather than in a later observability phase
    # because Phase 3 is the first time NEXORA spends money, and spend you
    # cannot see is spend you cannot control. They are cheap -- three nullable
    # columns, no new table, no new infrastructure. A dedicated `model_usage`
    # table (ARCHITECTURE.md §8) arrives when there is more to record than
    # this, such as tool calls and per-request latency.
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        # Defence in depth: the ORM already restricts `role`, but a script or
        # a psql session bypassing the ORM should not be able to write a value
        # the application cannot read.
        #
        # Declared here rather than via Enum(create_constraint=True) for a
        # concrete reason: a constraint the Enum generates is emitted as DDL
        # but does not appear in `Base.metadata` in a form Alembic can compare.
        # Autogenerate then sees a constraint in the database that the models
        # do not declare and proposes *dropping* it -- so `alembic check`
        # fails and the next autogenerated migration quietly removes it.
        # Explicit here, comparable there.
        CheckConstraint(
            "role IN ('user', 'assistant')",
            # Expanded by the naming convention in db/base.py to
            # ck_messages_message_role.
            name="message_role",
        ),
        # Two messages in one conversation cannot share a position. This is
        # what makes the ordering a guarantee rather than a convention: a bug
        # that tried to write a duplicate fails loudly instead of producing a
        # thread whose order depends on which row the database returns first.
        #
        # It also creates the composite index that every read needs -- "this
        # conversation's messages, in order" -- so the database never sorts
        # rows after fetching them, and no separate index is required.
        UniqueConstraint("conversation_id", "position"),
    )

    def __repr__(self) -> str:
        # SECURITY: never includes `content`. reprs end up in logs and
        # tracebacks, and message content is the most private thing here.
        return (
            f"<Message id={self.id} role={self.role.value} "
            f"position={self.position} conv={self.conversation_id}>"
        )
