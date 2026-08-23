"""Conversation and message business logic.

Every function that reads a conversation takes a `user_id` and filters on it.
That is not defensive habit, it is the authorization model: a conversation id
is a UUID, which is unguessable in practice, but "hard to guess" is not
"not allowed". Loading by id alone would be an insecure direct object
reference -- get hold of an id by any means and read someone else's chat.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import ChatMessage
from app.core.clock import utcnow
from app.core.logging import get_logger
from app.models.conversation import TITLE_MAX_LENGTH, Conversation
from app.models.message import Message, MessageRole

logger = get_logger(__name__)


def derive_title(first_message: str) -> str:
    """Make a conversation title out of the first thing the user said.

    Whitespace is collapsed so that a pasted multi-line block does not become
    a title containing newlines, and the result is truncated with an ellipsis
    rather than cut mid-word-count at the storage limit.
    """
    collapsed = " ".join(first_message.split())
    if not collapsed:
        return "New conversation"
    if len(collapsed) <= TITLE_MAX_LENGTH:
        return collapsed
    return collapsed[: TITLE_MAX_LENGTH - 1].rstrip() + "…"


async def create_conversation(
    db: AsyncSession, *, user_id: uuid.UUID, title: str | None = None
) -> Conversation:
    conversation = Conversation(user_id=user_id, title=title)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    logger.info("Conversation created", extra={"conversation_id": str(conversation.id)})
    return conversation


async def get_conversation(
    db: AsyncSession, *, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation | None:
    """Load one conversation **belonging to this user**, or None.

    Returning None for "someone else's conversation" rather than raising a
    distinct error is deliberate: the caller turns both it and "no such
    conversation" into the same 404, so the response cannot be used to learn
    whether an id exists.
    """
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            # The authorization. Never remove this to "simplify" the query.
            Conversation.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_conversations(
    db: AsyncSession, *, user_id: uuid.UUID
) -> Sequence[Conversation]:
    """This user's conversations, most recently active first."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    return result.scalars().all()


async def next_position(db: AsyncSession, *, conversation_id: uuid.UUID) -> int:
    """The position the next message in this conversation should take.

    Read-then-write, so two requests appending to the *same* conversation at
    the same moment can compute the same number. That is why
    `(conversation_id, position)` is unique: the second insert fails loudly
    with an IntegrityError instead of silently producing a thread whose order
    depends on which row the database happens to return first. Chat within one
    conversation is inherently serial -- the user waits for the reply -- so
    this is a guard against bugs rather than an expected path.
    """
    result = await db.execute(
        select(func.coalesce(func.max(Message.position), -1)).where(
            Message.conversation_id == conversation_id
        )
    )
    return int(result.scalar_one()) + 1


async def append_message(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    role: MessageRole,
    content: str,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> Message:
    """Add a message to the end of a conversation.

    Also bumps the conversation's `updated_at`. SQLAlchemy's `onupdate` fires
    only when the conversation row itself changes, and appending a message
    does not touch it -- so without this the sidebar would sort by "last
    renamed" rather than "last used".
    """
    message = Message(
        conversation_id=conversation_id,
        role=role,
        position=await next_position(db, conversation_id=conversation_id),
        content=content,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    db.add(message)

    conversation = await db.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.updated_at = utcnow()

    await db.commit()
    await db.refresh(message)
    return message


async def drop_trailing_assistant_message(
    db: AsyncSession, *, conversation_id: uuid.UUID
) -> bool:
    """Remove the last message if it is an assistant turn. Returns whether it was.

    What "regenerate" means: the previous answer is discarded and replaced,
    not kept alongside the new one. Keeping both would need a notion of
    alternative replies -- which reply is "the" answer, which one later turns
    build on -- and nothing in this phase needs that.

    Deleting the row rather than marking it superseded also keeps the
    `position` sequence contiguous, so the new reply simply takes the position
    the old one vacated.

    Returns False when the last message is already a user turn, which happens
    after a failed attempt that produced no text. That is not an error: there
    is simply nothing to remove before regenerating.
    """
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.position.desc())
        .limit(1)
    )
    last = result.scalar_one_or_none()

    if last is None or last.role is not MessageRole.ASSISTANT:
        return False

    await db.delete(last)
    await db.commit()
    logger.info(
        "Discarded an assistant reply for regeneration",
        extra={"conversation_id": str(conversation_id)},
    )
    return True


async def last_message(
    db: AsyncSession, *, conversation_id: uuid.UUID
) -> Message | None:
    """The most recent message in a conversation, or None if it is empty."""
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.position.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def load_history(
    db: AsyncSession, *, conversation_id: uuid.UUID
) -> list[ChatMessage]:
    """The conversation so far, in the provider-neutral form the gateway wants.

    COST: this sends the whole thread on every turn, because that is what
    gives a model memory of the conversation -- it is stateless between
    requests. The consequence is that a long conversation costs more per turn
    than a short one, and grows. Trimming or summarising history is Phase 9
    (memory); doing it naively here would silently drop context and make
    answers worse for a saving nobody asked for.
    """
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.position)
    )
    return [
        ChatMessage(role=message.role.value, content=message.content)
        for message in result.scalars().all()
    ]
