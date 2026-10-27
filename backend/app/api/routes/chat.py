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
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from app.ai.base import (
    ChatMessage,
    ModelAuthError,
    ModelProviderError,
    ModelRateLimitError,
    TextDelta,
    Usage,
    with_idle_timeout,
)
from app.ai.context import BASE_SYSTEM_PROMPT, build_system_prompt, citations_payload
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
    ConversationFileResponse,
    ConversationPage,
    ConversationResponse,
    EditMessageRequest,
    MessageResponse,
    RegenerateRequest,
    RenameConversationRequest,
)
from app.services.retrieval import RetrievedChunk, embed_query, search_chunks
from app.services.chat import (
    append_message,
    attach_files,
    conversation_files,
    detach_file,
    create_conversation,
    delete_conversation,
    derive_title,
    drop_trailing_assistant_message,
    get_conversation,
    get_message,
    last_message,
    list_conversations,
    load_history,
    rename_conversation,
    replace_user_message,
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


# Re-exported for tests and older imports. The prompt now lives beside the
# retrieval rules it has to stay consistent with -- see app/ai/context.py.
SYSTEM_PROMPT = BASE_SYSTEM_PROMPT

_CONVERSATION_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Conversation not found",
)

# The sidebar asks for one page at a time. The ceiling is what stops a client
# from turning "list my conversations" into "serialize everything I own".
DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100
# A search term longer than a title cannot usefully match one, and an
# unbounded term is an unbounded LIKE pattern to scan every row against.
MAX_SEARCH_LENGTH = 200


async def _retrieve_context(
    db: DbSession,
    *,
    query: str,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    file_ids: list[uuid.UUID] | None,
) -> list[RetrievedChunk]:
    """Find passages from this user's documents that bear on their question.

    Files attached to the conversation are searched first and always
    contribute their best passages; the rest of the user's library follows
    at the normal similarity floor.

    Failure is deliberately not fatal. If retrieval breaks -- the embedding
    provider is down, the vector index is rebuilding -- the user gets an answer
    without their documents rather than no answer at all. A degraded reply
    beats a 500, and the interface shows no sources, so nothing is claimed that
    was not read.
    """
    try:
        if file_ids:
            # An explicit scope from the client wins over everything else.
            return await search_chunks(
                db, query=query, user_id=user_id, file_ids=file_ids
            )

        attached = [
            file.id
            for file in await conversation_files(
                db, conversation_id=conversation_id, user_id=user_id
            )
        ]
        # Embedded once, shared by both searches below.
        vector = await embed_query(query) if query.strip() else None
        general = await search_chunks(
            db, query=query, user_id=user_id, query_vector=vector
        )
        if not attached:
            return general

        # Files added to this chat come first, and without the similarity
        # floor: "what is my email?" shares too few words with a resume to
        # clear it, but the person plainly means the file they put here.
        # Ranked by similarity within those files, capped separately.
        pinned = await search_chunks(
            db,
            query=query,
            user_id=user_id,
            file_ids=attached,
            limit=get_settings().conversation_file_top_k,
            min_similarity=-1.0,
            query_vector=vector,
        )
        seen = {" ".join(chunk.content.split()) for chunk in pinned}
        rest = [
            chunk
            for chunk in general
            if " ".join(chunk.content.split()) not in seen
        ]
        return [*pinned, *rest]
    except Exception:  # noqa: BLE001 - retrieval must not break chat
        logger.exception(
            "Retrieval failed; answering without documents",
            extra={"user_id": str(user_id)},
        )
        return []


def _enforce_chat_rate_limit(user_id: uuid.UUID) -> None:
    """Charge one unit against the user's chat quota, or raise 429.

    Shared by every endpoint that calls a model. They all cost the same, so
    they draw on the same counter -- separate counters would let a caller spend
    three times the intended budget by rotating between sending, regenerating
    and editing.
    """
    if chat_rate_limiter.allow(f"chat:{user_id}"):
        return

    logger.warning("Chat rate limit exceeded", extra={"user_id": str(user_id)})
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many messages. Please wait a moment and try again.",
        headers={"Retry-After": str(chat_rate_limiter.window_seconds)},
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
    retrieved: Sequence[RetrievedChunk] = (),
) -> AsyncIterator[str]:
    """Generate the reply, framing each fragment as an SSE event."""
    provider = get_provider()
    parts: list[str] = []
    usage: Usage | None = None
    failure: tuple[str, str] | None = None

    # Retrieved passages go in the *system* prompt inside explicit delimiters,
    # never concatenated into the user's message -- see app/ai/context.py for
    # why that separation is a security control rather than formatting.
    system_prompt = build_system_prompt(retrieved)

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

    if retrieved:
        # Sent before the answer, so the interface can show what NEXORA read
        # *while* it is answering. Built from the retrieval result, never
        # parsed out of the model's output -- a citation the model invented
        # must never become one the UI displays as real.
        yield _sse({"type": "sources", "sources": citations_payload(retrieved)})

    try:
        async for event in with_idle_timeout(
            provider.stream(history, system=system_prompt),
            seconds=get_settings().ai_stream_idle_timeout_seconds,
        ):
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
    _enforce_chat_rate_limit(user.id)

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

    if payload.attach_file_ids:
        # Before the message, so this very question already sees them.
        await attach_files(
            db,
            conversation_id=conversation.id,
            user_id=user.id,
            file_ids=payload.attach_file_ids,
        )

    user_message = await append_message(
        db,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=payload.content,
    )

    # Loaded *before* streaming starts, while the request-scoped session is
    # still open. It already includes the message just appended.
    history = await load_history(db, conversation_id=conversation.id)

    # Retrieval also happens here, for the same reason: the generator below
    # runs after this handler returns, by which point `db` is gone.
    retrieved = await _retrieve_context(
        db,
        query=payload.content,
        user_id=user.id,
        conversation_id=conversation.id,
        file_ids=payload.file_ids,
    )

    return StreamingResponse(
        _stream_reply(
            session_factory, conversation.id, user_message.id, history, retrieved
        ),
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
    _enforce_chat_rate_limit(user.id)

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

    # Retrieval runs again against the same question, rather than reusing what
    # the first attempt found. Documents may have been uploaded or deleted
    # since, and a regenerated answer citing a file the user has removed would
    # be worse than a slightly slower one.
    retrieved = await _retrieve_context(
        db,
        query=question.content,
        user_id=user.id,
        conversation_id=conversation.id,
        file_ids=None,
    )

    return StreamingResponse(
        # No *new* user message exists, so the `start` event reports the
        # existing question -- which is the turn being answered.
        _stream_reply(
            session_factory, conversation.id, question.id, history, retrieved
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/edit",
    summary="Rewrite a question and answer it again",
    description=(
        "Replaces the text of one of your own messages, discards every turn "
        "after it, and streams a new reply. Returns a `text/event-stream`."
    ),
    responses={
        404: {"description": "No such conversation or message for this user"},
        409: {"description": "That message is not an editable question"},
        429: {"description": "Too many requests"},
    },
)
async def edit_message(
    payload: EditMessageRequest,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    session_factory: SessionFactory,
) -> StreamingResponse:
    """Edit a question, rewind the thread to it, and answer again."""
    _enforce_chat_rate_limit(user.id)

    conversation = await get_conversation(
        db, conversation_id=payload.conversation_id, user_id=user.id
    )
    if conversation is None:
        raise _CONVERSATION_NOT_FOUND

    message = await get_message(
        db, conversation_id=conversation.id, message_id=payload.message_id
    )
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )

    # SECURITY: only the user's own turns are editable. Allowing an assistant
    # message to be rewritten would let a caller put words in the model's
    # mouth -- and those words come back as context on every later turn, so a
    # forged reply becomes something the model treats as its own prior
    # statement. That is self-inflicted prompt injection with a persistent
    # store behind it.
    if message.role is not MessageRole.USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only your own messages can be edited.",
        )

    await replace_user_message(
        db, conversation=conversation, message=message, content=payload.content
    )

    history = await load_history(db, conversation_id=conversation.id)

    # Retrieved against the *edited* wording -- the whole point of the edit is
    # that the question changed, so passages found for the old text would
    # answer a question no longer being asked.
    retrieved = await _retrieve_context(
        db,
        query=payload.content,
        user_id=user.id,
        conversation_id=conversation.id,
        file_ids=None,
    )

    return StreamingResponse(
        _stream_reply(
            session_factory, conversation.id, message.id, history, retrieved
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get(
    "/conversations",
    response_model=ConversationPage,
    summary="List or search this user's conversations",
)
async def get_conversations(
    db: DbSession,
    user: CurrentUser,
    q: Annotated[
        str | None,
        Query(
            max_length=MAX_SEARCH_LENGTH,
            description="Match conversations by title or message text.",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConversationPage:
    """One page of conversations, newest activity first.

    `limit` is capped by the route rather than trusted from the client, so a
    request for a million rows is rejected at the boundary instead of becoming
    a million-row query.
    """
    # A search of only spaces is not a search. Treating it as one would run a
    # `%  %` scan and return nothing, which looks like a bug to the user.
    term = q.strip() if q else None

    conversations, has_more = await list_conversations(
        db, user_id=user.id, query=term or None, limit=limit, offset=offset
    )
    return ConversationPage(
        items=[ConversationResponse.model_validate(c) for c in conversations],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Rename a conversation",
    responses={404: {"description": "No such conversation for this user"}},
)
async def patch_conversation(
    conversation_id: uuid.UUID,
    payload: RenameConversationRequest,
    db: DbSession,
    user: CurrentUser,
) -> ConversationResponse:
    conversation = await get_conversation(
        db, conversation_id=conversation_id, user_id=user.id
    )
    if conversation is None:
        raise _CONVERSATION_NOT_FOUND

    renamed = await rename_conversation(db, conversation=conversation, title=payload.title)
    return ConversationResponse.model_validate(renamed)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation and all of its messages",
    responses={404: {"description": "No such conversation for this user"}},
)
async def remove_conversation(
    conversation_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    conversation = await get_conversation(
        db, conversation_id=conversation_id, user_id=user.id
    )
    # 404 rather than a silent success. "Delete something that is not yours"
    # and "delete something that does not exist" must stay indistinguishable,
    # or the response becomes a way to test which ids are real.
    if conversation is None:
        raise _CONVERSATION_NOT_FOUND

    await delete_conversation(db, conversation=conversation)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
        files=[
            ConversationFileResponse(
                id=file.id,
                filename=file.filename,
                status=file.status.value,
                has_text=bool(file.extracted_text),
            )
            for file in await conversation_files(
                db, conversation_id=conversation.id, user_id=user.id
            )
        ],
    )


@router.delete(
    "/conversations/{conversation_id}/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a file from a conversation (the file itself is kept)",
    responses={404: {"description": "No such conversation, or file not in it"}},
)
async def remove_conversation_file(
    conversation_id: uuid.UUID, file_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    conversation = await get_conversation(
        db, conversation_id=conversation_id, user_id=user.id
    )
    if conversation is None:
        raise _CONVERSATION_NOT_FOUND
    if not await detach_file(db, conversation_id=conversation.id, file_id=file_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not in this conversation",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
