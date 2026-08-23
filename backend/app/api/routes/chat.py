"""Chat endpoints.

`POST /chat` streams the reply back as Server-Sent Events. The other two
endpoints load history, so a browser refresh does not lose the conversation.

**Why SSE and not WebSocket.** The problem is one-way: the server sends tokens,
the client sends nothing until the next turn. SSE is plain HTTP, so it reuses
the cookie auth, the CORS allow-list and the CSRF check already in place, and
it can be debugged with `curl`. A WebSocket would be a second protocol with a
second authentication path for no benefit this phase can use.

**Why POST and not EventSource.** The browser's `EventSource` API only issues
GET requests and cannot set headers, and a message does not belong in a query
string. The frontend reads the stream with `fetch` instead.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.ai.base import (
    ChatMessage,
    ModelAuthError,
    ModelProviderError,
    ModelRateLimitError,
    TextDelta,
    Usage,
)
from app.ai.gateway import get_provider
from app.api.dependencies import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rate_limit import SlidingWindowRateLimiter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import get_db_session_factory
from app.models.message import MessageRole
from app.schemas.chat import (
    ChatRequest,
    ConversationDetailResponse,
    ConversationResponse,
    MessageResponse,
    RegenerateRequest,
)
from app.services.chat import (
    append_message,
    create_conversation,
    derive_title,
    drop_trailing_assistant_message,
    get_conversation,
    last_message,
    list_conversations,
    load_history,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])

_settings = get_settings()

# Keyed by user id rather than address -- see the note in config.py.
chat_rate_limiter = SlidingWindowRateLimiter(
    max_attempts=_settings.chat_rate_limit_attempts,
    window_seconds=_settings.chat_rate_limit_window_seconds,
)


def reset_chat_rate_limits() -> None:
    """Clear the chat limiter. Used by tests to isolate one case from the next."""
    chat_rate_limiter.clear()


SYSTEM_PROMPT = (
    "You are NEXORA, an intelligent AI workspace. Be accurate and concise. "
    "If you do not know something, say so rather than guessing."
)

_CONVERSATION_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Conversation not found",
)


def _sse(payload: dict[str, Any]) -> str:
    """Frame one Server-Sent Event.

    The body is JSON rather than raw text for two reasons. A literal newline
    inside `data:` terminates the field, so streaming raw model output would
    corrupt the stream the first time it produced a line break -- JSON escapes
    them. And events need to carry more than text: which conversation was
    created, and whether the turn failed halfway through.
    """
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


SessionFactory = Annotated[
    "async_sessionmaker[AsyncSession]", Depends(get_db_session_factory)
]


async def _persist_reply(
    session_factory: async_sessionmaker[AsyncSession],
    conversation_id: uuid.UUID,
    text: str,
    usage: Usage | None,
) -> uuid.UUID | None:
    """Save the assistant's reply, using a session of its own.

    **This is the subtle part of the whole endpoint.** The generator below runs
    *after* the route handler has returned -- that is what streaming means --
    and by then FastAPI has already torn down the request-scoped `db`
    dependency. Reusing it here would fail once the response started, which is
    the kind of bug that never shows up in a non-streaming test. So the
    generator opens its own session from the factory and closes it itself.
    """
    if not text:
        # Nothing was generated -- the provider failed on the first token, or
        # the client vanished immediately. An empty assistant turn would just
        # be noise in the thread.
        return None

    try:
        async with session_factory() as session:
            message = await append_message(
                session,
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=text,
                model=usage.model if usage else None,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
            )
            return message.id
    except Exception:  # noqa: BLE001 - persistence must not kill the response
        logger.exception(
            "Failed to persist an assistant reply",
            extra={"conversation_id": str(conversation_id)},
        )
        return None


async def _stream_reply(
    session_factory: async_sessionmaker[AsyncSession],
    conversation_id: uuid.UUID,
    user_message_id: uuid.UUID,
    history: list[ChatMessage],
) -> AsyncIterator[str]:
    """Generate the reply, framing each fragment as an SSE event."""
    provider = get_provider()
    parts: list[str] = []
    usage: Usage | None = None
    failure: tuple[str, str] | None = None

    # Sent first so the client learns the conversation id immediately. When
    # the request omitted one, this is the only way it finds out which
    # conversation its message landed in.
    yield _sse(
        {
            "type": "start",
            "conversation_id": str(conversation_id),
            "user_message_id": str(user_message_id),
        }
    )

    try:
        async for event in provider.stream(history, system=SYSTEM_PROMPT):
            if isinstance(event, TextDelta):
                parts.append(event.text)
                yield _sse({"type": "delta", "text": event.text})
            elif isinstance(event, Usage):
                usage = event
    except ModelAuthError:
        # An operator problem -- a missing or rejected key -- never the user's
        # fault, so the message must not suggest they did something wrong.
        failure = ("provider_unavailable", "The assistant is unavailable right now.")
    except ModelRateLimitError:
        failure = (
            "rate_limited",
            "The assistant is busy right now. Please try again in a moment.",
        )
    except ModelProviderError:
        failure = ("provider_unavailable", "The assistant is unavailable right now.")
    finally:
        # Runs on success, on provider failure, *and* when the client
        # disconnects mid-stream (Starlette closes the generator, raising
        # GeneratorExit at the yield above). That last case is the reason this
        # is a `finally`: the tokens were generated and billed whether or not
        # anyone was still listening, so throwing them away would mean the user
        # pays twice for the same answer. Note it must not `yield` -- yielding
        # while closing raises RuntimeError.
        assistant_message_id = await _persist_reply(
            session_factory, conversation_id, "".join(parts), usage
        )

    if failure is not None:
        code, message = failure
        yield _sse({"type": "error", "code": code, "message": message})
        return

    yield _sse(
        {
            "type": "done",
            "conversation_id": str(conversation_id),
            "message_id": str(assistant_message_id) if assistant_message_id else None,
            "model": usage.model if usage else None,
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
        }
    )


@router.post(
    "",
    summary="Send a message and stream the reply",
    description=(
        "Returns a `text/event-stream`. Each event is a JSON object with a "
        "`type` of `start`, `delta`, `done`, or `error`."
    ),
    responses={
        404: {"description": "No such conversation for this user"},
        429: {"description": "Too many requests"},
    },
)
async def send_message(
    payload: ChatRequest,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    session_factory: SessionFactory,
) -> StreamingResponse:
    """Append the user's message and stream the assistant's reply."""
    # Keyed by user: the cost belongs to an account, not an address.
    if not chat_rate_limiter.allow(f"chat:{user.id}"):
        logger.warning("Chat rate limit exceeded", extra={"user_id": str(user.id)})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many messages. Please wait a moment and try again.",
            headers={"Retry-After": str(chat_rate_limiter.window_seconds)},
        )

    if payload.conversation_id is None:
        conversation = await create_conversation(
            db, user_id=user.id, title=derive_title(payload.content)
        )
    else:
        conversation = await get_conversation(
            db, conversation_id=payload.conversation_id, user_id=user.id
        )
        # The same 404 whether the conversation does not exist or belongs to
        # someone else. Distinguishing them would let anyone probe for which
        # ids are real.
        if conversation is None:
            raise _CONVERSATION_NOT_FOUND

    user_message = await append_message(
        db,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=payload.content,
    )

    # Loaded *before* streaming starts, while the request-scoped session is
    # still open. It already includes the message just appended.
    history = await load_history(db, conversation_id=conversation.id)

    return StreamingResponse(
        _stream_reply(session_factory, conversation.id, user_message.id, history),
        media_type="text/event-stream",
        headers={
            # Without this a proxy may hold the whole response and deliver it
            # at once, which looks exactly like streaming being broken.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/regenerate",
    summary="Replace the last reply with a fresh one",
    description=(
        "Discards the most recent assistant reply and streams a new answer to "
        "the same question. Returns a `text/event-stream`, exactly like /chat."
    ),
    responses={
        404: {"description": "No such conversation for this user"},
        409: {"description": "There is no question to answer"},
        429: {"description": "Too many requests"},
    },
)
async def regenerate(
    payload: RegenerateRequest,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    session_factory: SessionFactory,
) -> StreamingResponse:
    """Throw away the last answer and produce another one.

    Rate limited on the same counter as sending, because it costs exactly the
    same as sending: a full request to the model. Regenerating is also the
    easiest thing in the product to sit and hammer.
    """
    if not chat_rate_limiter.allow(f"chat:{user.id}"):
        logger.warning("Chat rate limit exceeded", extra={"user_id": str(user.id)})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many messages. Please wait a moment and try again.",
            headers={"Retry-After": str(chat_rate_limiter.window_seconds)},
        )

    conversation = await get_conversation(
        db, conversation_id=payload.conversation_id, user_id=user.id
    )
    if conversation is None:
        raise _CONVERSATION_NOT_FOUND

    # Returns False when the previous attempt produced no text at all, in which
    # case the user turn is already last and there is nothing to discard.
    await drop_trailing_assistant_message(db, conversation_id=conversation.id)

    history = await load_history(db, conversation_id=conversation.id)
    question = await last_message(db, conversation_id=conversation.id)

    # After dropping the reply the thread must end on a question. An empty
    # conversation, or one somehow ending on an assistant turn, has nothing to
    # regenerate -- and sending it to a provider would be rejected there with a
    # far less obvious message.
    if question is None or question.role is not MessageRole.USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There is no question to regenerate an answer for.",
        )

    return StreamingResponse(
        # No *new* user message exists, so the `start` event reports the
        # existing question -- which is the turn being answered.
        _stream_reply(session_factory, conversation.id, question.id, history),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get(
    "/conversations",
    response_model=list[ConversationResponse],
    summary="List this user's conversations",
)
async def get_conversations(db: DbSession, user: CurrentUser) -> list[ConversationResponse]:
    conversations = await list_conversations(db, user_id=user.id)
    return [ConversationResponse.model_validate(c) for c in conversations]


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="Load one conversation with its messages",
    responses={404: {"description": "No such conversation for this user"}},
)
async def get_conversation_detail(
    conversation_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> ConversationDetailResponse:
    conversation = await get_conversation(
        db, conversation_id=conversation_id, user_id=user.id
    )
    if conversation is None:
        raise _CONVERSATION_NOT_FOUND

    await db.refresh(conversation, ["messages"])

    return ConversationDetailResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[MessageResponse.model_validate(m) for m in conversation.messages],
    )
