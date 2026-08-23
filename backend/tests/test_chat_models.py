"""Tests for the conversation and message models.

The focus is the properties that are easy to get wrong and expensive to
discover late: ownership, cascade behaviour, deterministic ordering, and the
database refusing values the application cannot read.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.user import User
from app.services.auth import create_user

PASSWORD = "a-sufficiently-long-password"


async def make_user(db: AsyncSession, email: str = "ada@example.com") -> User:
    return await create_user(db, email=email, password=PASSWORD)


async def make_conversation(
    db: AsyncSession, user: User, title: str | None = "A chat"
) -> Conversation:
    conversation = Conversation(user_id=user.id, title=title)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


class TestConversation:
    async def test_belongs_to_its_user(self, db: AsyncSession) -> None:
        user = await make_user(db)

        conversation = await make_conversation(db, user)

        assert conversation.user_id == user.id

    async def test_title_may_be_absent(self, db: AsyncSession) -> None:
        """NULL means 'not titled yet', which is genuinely different from an
        empty title. Phase 4 adds renaming."""
        user = await make_user(db)

        conversation = await make_conversation(db, user, title=None)

        assert conversation.title is None

    async def test_repr_does_not_leak_the_title(self, db: AsyncSession) -> None:
        """SECURITY: the title is derived from user content, and reprs end up
        in logs and tracebacks."""
        user = await make_user(db)
        conversation = await make_conversation(db, user, title="my private topic")

        assert "my private topic" not in repr(conversation)

    async def test_cannot_belong_to_a_user_who_does_not_exist(
        self, db: AsyncSession
    ) -> None:
        db.add(Conversation(user_id=uuid.uuid4(), title="orphan"))

        with pytest.raises(IntegrityError):
            await db.commit()


class TestMessage:
    async def test_stores_role_and_content(self, db: AsyncSession) -> None:
        user = await make_user(db)
        conversation = await make_conversation(db, user)

        db.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                position=0,
                content="hello",
            )
        )
        await db.commit()

        stored = (await db.execute(select(Message))).scalar_one()
        assert stored.role is MessageRole.USER
        assert stored.content == "hello"

    async def test_role_is_stored_as_its_value_not_its_python_name(
        self, db: AsyncSession
    ) -> None:
        """Without `values_callable`, SQLAlchemy writes "USER" while the
        application reads "user". Everything looks fine until something reads
        the column directly -- a report, a migration, another service."""
        user = await make_user(db)
        conversation = await make_conversation(db, user)
        db.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                position=0,
                content="hi",
            )
        )
        await db.commit()

        raw = (await db.execute(text("SELECT role FROM messages"))).scalar_one()

        assert raw == "assistant"

    async def test_the_database_rejects_an_unknown_role(
        self, db: AsyncSession
    ) -> None:
        """Defence in depth. The ORM already restricts this, so the insert
        deliberately bypasses it -- that is exactly the path a maintenance
        script or a psql session would take."""
        user = await make_user(db)
        conversation = await make_conversation(db, user)

        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "INSERT INTO messages (id, conversation_id, role, position,"
                    " content, created_at)"
                    " VALUES (:id, :cid, 'hacker', 0, 'x', :now)"
                ),
                {"id": str(uuid.uuid4()), "cid": str(conversation.id), "now": utcnow()},
            )
            await db.commit()

    async def test_cost_columns_default_to_null_on_a_user_turn(
        self, db: AsyncSession
    ) -> None:
        user = await make_user(db)
        conversation = await make_conversation(db, user)
        db.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                position=0,
                content="hello",
            )
        )
        await db.commit()

        stored = (await db.execute(select(Message))).scalar_one()
        assert stored.model is None
        assert stored.input_tokens is None
        assert stored.output_tokens is None

    async def test_cost_columns_are_recorded_on_an_assistant_turn(
        self, db: AsyncSession
    ) -> None:
        """Phase 3 is the first time NEXORA spends money. Spend you cannot see
        is spend you cannot control."""
        user = await make_user(db)
        conversation = await make_conversation(db, user)
        db.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                position=0,
                content="an answer",
                model="claude-opus-5",
                input_tokens=12,
                output_tokens=34,
            )
        )
        await db.commit()

        stored = (await db.execute(select(Message))).scalar_one()
        assert stored.model == "claude-opus-5"
        assert stored.input_tokens == 12
        assert stored.output_tokens == 34

    async def test_repr_never_includes_the_content(self, db: AsyncSession) -> None:
        """SECURITY: message content is the most private data in the system."""
        message = Message(
            conversation_id=uuid.uuid4(),
            role=MessageRole.USER,
            position=0,
            content="my medical history",
        )

        assert "my medical history" not in repr(message)

    async def test_cannot_belong_to_a_conversation_that_does_not_exist(
        self, db: AsyncSession
    ) -> None:
        db.add(
            Message(
                conversation_id=uuid.uuid4(),
                role=MessageRole.USER,
                position=0,
                content="orphan",
            )
        )

        with pytest.raises(IntegrityError):
            await db.commit()


class TestOrderingAndCascades:
    async def test_messages_load_in_position_order(self, db: AsyncSession) -> None:
        """Inserted deliberately out of order, and in a single commit so every
        row shares a timestamp -- which is exactly the case that a
        created_at sort gets wrong."""
        user = await make_user(db)
        conversation = await make_conversation(db, user)
        for index in [3, 0, 4, 1, 2]:
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
                    position=index,
                    content=f"message {index}",
                )
            )
        await db.commit()

        loaded = await db.get(Conversation, conversation.id)
        assert loaded is not None
        await db.refresh(loaded, ["messages"])

        assert [m.content for m in loaded.messages] == [
            f"message {index}" for index in range(5)
        ]

    async def test_two_messages_cannot_share_a_position(
        self, db: AsyncSession
    ) -> None:
        """Makes the ordering a guarantee rather than a convention: a bug that
        wrote a duplicate fails loudly instead of producing a thread whose
        order depends on which row the database happens to return first."""
        user = await make_user(db)
        conversation = await make_conversation(db, user)
        for role in (MessageRole.USER, MessageRole.ASSISTANT):
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role=role,
                    position=0,
                    content="clash",
                )
            )

        with pytest.raises(IntegrityError):
            await db.commit()

    async def test_the_same_position_is_fine_in_different_conversations(
        self, db: AsyncSession
    ) -> None:
        user = await make_user(db)
        first = await make_conversation(db, user, title="one")
        second = await make_conversation(db, user, title="two")
        for conversation in (first, second):
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    position=0,
                    content="hello",
                )
            )

        await db.commit()

        assert len((await db.execute(select(Message))).scalars().all()) == 2

    async def test_deleting_a_conversation_deletes_its_messages(
        self, db: AsyncSession
    ) -> None:
        user = await make_user(db)
        conversation = await make_conversation(db, user)
        db.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                position=0,
                content="hello",
            )
        )
        await db.commit()

        await db.delete(conversation)
        await db.commit()

        assert (await db.execute(select(Message))).scalars().all() == []

    async def test_deleting_a_user_deletes_their_conversations_and_messages(
        self, db: AsyncSession
    ) -> None:
        """Deleting an account must not leave its chat history readable."""
        user = await make_user(db)
        conversation = await make_conversation(db, user)
        db.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                position=0,
                content="hello",
            )
        )
        await db.commit()

        await db.delete(user)
        await db.commit()

        assert (await db.execute(select(Conversation))).scalars().all() == []
        assert (await db.execute(select(Message))).scalars().all() == []

    async def test_one_users_conversations_are_separate_from_anothers(
        self, db: AsyncSession
    ) -> None:
        """The isolation this whole design exists to provide."""
        ada = await make_user(db, "ada@example.com")
        grace = await make_user(db, "grace@example.com")
        await make_conversation(db, ada, title="ada's chat")
        await make_conversation(db, grace, title="grace's chat")

        adas = (
            (await db.execute(select(Conversation).where(Conversation.user_id == ada.id)))
            .scalars()
            .all()
        )

        assert [c.title for c in adas] == ["ada's chat"]
