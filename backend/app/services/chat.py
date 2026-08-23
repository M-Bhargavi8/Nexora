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

from sqlalchemy import delete, func, or_, select
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


def _like_pattern(term: str) -> str:
    r"""Turn a search term into a safe `LIKE` pattern.

    SQLAlchemy parameterizes the value, so there is no SQL injection here. But
    parameterization does **not** neutralize `LIKE` wildcards, and that is a
    correctness bug waiting to happen: searching for `100%` would match every
    conversation, and `_` would match any single character. Both are ordinary
    things to type. So the metacharacters are escaped, with the escape
    character itself escaped first -- doing it in the other order would double
    the backslashes it just inserted.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def list_conversations(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> tuple[Sequence[Conversation], bool]:
    """One page of this user's conversations, most recently active first.

    Returns the page and whether another one follows.

    `query` matches the title *or* any message in the thread. Title-only search
    would be cheaper but nearly useless -- titles are generated from the first
    message, so the thing you remember saying is usually not in one.

    COST: `%term%` cannot use a B-tree index, so this is a sequential scan.
    That is the honest trade for substring matching, and it is fine at the
    scale of one user's conversations. Ranked full-text search (a `tsvector`
    column plus a GIN index) is the upgrade when it stops being fine, and
    meaning-based search arrives with embeddings in Phase 6.
    """
    statement = select(Conversation).where(Conversation.user_id == user_id)

    if query:
        pattern = _like_pattern(query)
        statement = statement.where(
            or_(
                Conversation.title.ilike(pattern, escape="\\"),
                # EXISTS rather than a JOIN: a thread that mentions the term in
                # twelve messages must still appear once, and a JOIN would
                # return it twelve times.
                select(Message.id)
                .where(
                    Message.conversation_id == Conversation.id,
                    Message.content.ilike(pattern, escape="\\"),
                )
                .exists(),
            )
        )

    result = await db.execute(
        statement
        # `id` breaks ties. Without it, two conversations sharing a timestamp
        # could come back in a different order on each request, which makes
        # pagination drop or repeat rows across pages.
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .offset(offset)
        # One extra row, purely to answer "is there another page?" -- see the
        # note on ConversationPage.
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    return rows[:limit], has_more


async def rename_conversation(
    db: AsyncSession, *, conversation: Conversation, title: str
) -> Conversation:
    """Give a conversation a new title.

    Deliberately does not touch `updated_at`: the sidebar is ordered by when a
    conversation was last *used*, and renaming an old thread should not make it
    look like the newest one.
    """
    conversation.title = title
    await db.commit()
    await db.refresh(conversation)
    logger.info(
        "Conversation renamed", extra={"conversation_id": str(conversation.id)}
    )
    return conversation


async def delete_conversation(
    db: AsyncSession, *, conversation: Conversation
) -> None:
    """Delete a conversation and, by cascade, every message in it.

    A real delete, not a soft one. A user who deletes a conversation means it
    to be gone, and keeping a hidden copy they believe is deleted would be
    exactly the thing they did not consent to. Retention for auditing, if it is
    ever needed, has to be a stated policy rather than a silent default.
    """
    await db.delete(conversation)
    await db.commit()
    logger.info(
        "Conversation deleted", extra={"conversation_id": str(conversation.id)}
    )


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


async def get_message(
    db: AsyncSession, *, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> Message | None:
    """One message, but only if it belongs to the given conversation.

    The `conversation_id` filter is the authorization. The caller has already
    proved it owns *this conversation*; loading a message by id alone would
    happily return one from somebody else's thread.
    """
    result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
        )
    )
    return result.scalar_one_or_none()


async def replace_user_message(
    db: AsyncSession, *, conversation: Conversation, message: Message, content: str
) -> None:
    """Rewrite a question and discard everything that came after it.

    **Why the truncation.** Once a question changes, every turn after it was
    answering something that was never asked. Leaving them would produce a
    transcript that is simply untrue -- a reply visibly responding to text that
    is no longer above it -- and, worse, that false history would be sent to
    the model as context on the next turn. So editing rewinds the conversation
    to that point and it moves forward again from there.

    The alternative is branching: keep both versions as a tree and let the user
    switch between them. That is a real feature, and a much larger one -- it
    needs a notion of which branch is active, which turns belong to it, and a
    UI for moving between them. This phase takes the same position editing took
    in `drop_trailing_assistant_message`: replace, do not branch.

    Retitles the conversation when the *first* message changes, since the title
    was derived from it and would otherwise still advertise the old question.
    """
    message.content = content

    # Everything strictly after the edited turn, which includes the reply that
    # answered the old wording.
    await db.execute(
        delete(Message).where(
            Message.conversation_id == conversation.id,
            Message.position > message.position,
        )
    )

    if message.position == 0:
        conversation.title = derive_title(content)

    # One commit for the whole rewind. Committing the new text separately would
    # leave a window in which the conversation held an edited question still
    # followed by the answer to the old one.
    await db.commit()
    logger.info(
        "Question edited; later turns discarded",
        extra={
            "conversation_id": str(conversation.id),
            "from_position": message.position,
        },
    )


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
