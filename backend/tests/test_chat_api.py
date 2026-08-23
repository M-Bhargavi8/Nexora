"""End-to-end tests for the chat endpoints.

These drive the real HTTP surface, including the Server-Sent Events stream.
The offline echo provider answers, so nothing here calls a paid API.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import (
    ChatMessage,
    ModelAuthError,
    ModelProvider,
    ModelRateLimitError,
    ModelUnavailableError,
    TextDelta,
)
from app.ai.providers.echo import EchoProvider
from app.api.routes.chat import _stream_reply
from app.models.message import Message, MessageRole
from app.models.user import User
from app.schemas.chat import MAX_MESSAGE_LENGTH
from app.services.auth import create_user
from app.services.chat import append_message, create_conversation, load_history

API = "/api/v1"
EMAIL = "ada@example.com"
PASSWORD = "a-sufficiently-long-password"


def _full_echo_reply_length() -> int:
    """Length of a complete echo reply, computed rather than hard-coded so it
    cannot drift out of date when the canned text changes."""
    import asyncio

    async def run() -> str:
        from app.ai.base import TextDelta

        parts = [
            event.text
            async for event in EchoProvider().stream([ChatMessage("user", "hello")])
            if isinstance(event, TextDelta)
        ]
        return "".join(parts)

    return len(asyncio.run(run()))


FULL_ECHO_REPLY_LENGTH = _full_echo_reply_length()


async def sign_up(client: AsyncClient, email: str = EMAIL) -> None:
    response = await client.post(
        f"{API}/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201


async def stream_chat(
    client: AsyncClient, content: str, conversation_id: str | None = None
) -> list[dict[str, Any]]:
    """Send a message and collect every SSE event as parsed JSON."""
    payload: dict[str, Any] = {"content": content}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id

    events: list[dict[str, Any]] = []
    async with client.stream("POST", f"{API}/chat", json=payload) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def texts(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "delta")


def first(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(e for e in events if e["type"] == kind)


class TestAuthenticationRequired:
    """Chat is a paid, user-owned resource. None of it is public."""

    async def test_sending_a_message_requires_a_session(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(f"{API}/chat", json={"content": "hello"})

        assert response.status_code == 401

    async def test_listing_conversations_requires_a_session(
        self, client: AsyncClient
    ) -> None:
        assert (await client.get(f"{API}/chat/conversations")).status_code == 401

    async def test_reading_a_conversation_requires_a_session(
        self, client: AsyncClient
    ) -> None:
        response = await client.get(
            f"{API}/chat/conversations/11111111-1111-1111-1111-111111111111"
        )

        assert response.status_code == 401


class TestStreaming:
    async def test_streams_start_deltas_and_done(self, client: AsyncClient) -> None:
        await sign_up(client)

        events = await stream_chat(client, "hello there")

        assert events[0]["type"] == "start"
        assert events[-1]["type"] == "done"
        assert sum(e["type"] == "delta" for e in events) > 1, (
            "a single delta would mean the reply was buffered, not streamed"
        )

    async def test_the_reply_reaches_the_client(self, client: AsyncClient) -> None:
        await sign_up(client)

        events = await stream_chat(client, "hello there")

        assert "offline echo provider" in texts(events)

    async def test_the_provider_receives_the_users_message(
        self, client: AsyncClient
    ) -> None:
        """The echo provider repeats the last user turn, which proves the
        conversation actually reached it rather than a canned reply being
        produced regardless of input."""
        await sign_up(client)

        events = await stream_chat(client, "marmalade")

        assert "marmalade" in texts(events)

    async def test_start_announces_the_new_conversation_id(
        self, client: AsyncClient
    ) -> None:
        """When the request omits a conversation id, this event is the only
        way the client learns where its message landed."""
        await sign_up(client)

        events = await stream_chat(client, "hello")

        assert first(events, "start")["conversation_id"]

    async def test_done_reports_which_model_answered(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client)

        events = await stream_chat(client, "hello")

        assert first(events, "done")["model"] == "echo"


class TestPersistence:
    async def test_both_turns_are_stored_in_order(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        await sign_up(client)

        await stream_chat(client, "hello there")

        stored = (
            (await db.execute(select(Message).order_by(Message.position)))
            .scalars()
            .all()
        )
        assert [(m.position, m.role) for m in stored] == [
            (0, MessageRole.USER),
            (1, MessageRole.ASSISTANT),
        ]
        assert stored[0].content == "hello there"

    async def test_the_assistant_turn_records_what_produced_it(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """Canned replies must stay identifiable in the database, so they can
        never later be mistaken for something a real model said."""
        await sign_up(client)

        await stream_chat(client, "hello")

        assistant = (
            await db.execute(
                select(Message).where(Message.role == MessageRole.ASSISTANT)
            )
        ).scalar_one()
        assert assistant.model == "echo"

    async def test_a_second_message_continues_the_same_conversation(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        await sign_up(client)
        first_events = await stream_chat(client, "first")
        conversation_id = first(first_events, "start")["conversation_id"]

        second_events = await stream_chat(client, "second", conversation_id)

        assert first(second_events, "start")["conversation_id"] == conversation_id
        positions = sorted(
            m.position for m in (await db.execute(select(Message))).scalars().all()
        )
        assert positions == [0, 1, 2, 3]

    async def test_the_conversation_is_titled_from_the_first_message(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client)

        await stream_chat(client, "How do embeddings work?")

        listed = (await client.get(f"{API}/chat/conversations")).json()
        assert listed[0]["title"] == "How do embeddings work?"



class TestDisconnectHandling:
    """What happens when the client goes away mid-reply.

    These drive the generator directly rather than over HTTP. ASGITransport
    calls the app in-process and does not simulate a disconnect -- through it
    the stream always runs to completion, so an HTTP-level version of these
    tests passes without exercising anything. (It did, on first writing.)
    Closing the generator is precisely what Starlette does when a real client
    disappears.
    """

    async def _prepare(
        self, db: AsyncSession
    ) -> tuple[Any, Any, list[ChatMessage]]:
        user = await create_user(db, email=EMAIL, password=PASSWORD)
        conversation = await create_conversation(db, user_id=user.id, title="t")
        user_message = await append_message(
            db,
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="hello",
        )
        history = await load_history(db, conversation_id=conversation.id)
        return conversation, user_message, history

    async def _assistant(self, db: AsyncSession) -> Message | None:
        return (
            await db.execute(
                select(Message).where(Message.role == MessageRole.ASSISTANT)
            )
        ).scalar_one_or_none()

    async def test_a_partial_reply_is_kept(
        self,
        db: AsyncSession,
        session_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The tokens were generated and billed whether or not anyone was
        still listening. Discarding them would make the user pay twice for the
        same answer."""
        monkeypatch.setattr(
            "app.api.routes.chat.get_provider", lambda: EchoProvider()
        )
        conversation, user_message, history = await self._prepare(db)

        stream = _stream_reply(
            session_factory, conversation.id, user_message.id, history
        )
        seen = 0
        async for _event in stream:
            seen += 1
            if seen >= 3:  # start + two deltas, then walk away
                break
        await stream.aclose()

        assistant = await self._assistant(db)
        assert assistant is not None, "the partial reply was thrown away"
        assert 0 < len(assistant.content) < FULL_ECHO_REPLY_LENGTH, (
            "expected a genuinely partial reply; storing the whole thing would "
            "mean the stream was never actually interrupted"
        )

    async def test_nothing_is_stored_when_no_text_was_generated(
        self,
        db: AsyncSession,
        session_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An empty assistant turn would just be noise in the thread."""
        monkeypatch.setattr(
            "app.api.routes.chat.get_provider", lambda: EchoProvider()
        )
        conversation, user_message, history = await self._prepare(db)

        stream = _stream_reply(
            session_factory, conversation.id, user_message.id, history
        )
        await stream.__anext__()  # the `start` event only, before any text
        await stream.aclose()

        assert await self._assistant(db) is None


class TestUserIsolation:
    """The reason conversations carry user_id at all."""

    async def test_another_users_conversation_cannot_be_written_to(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client, "ada@example.com")
        events = await stream_chat(client, "ada's private message")
        conversation_id = first(events, "start")["conversation_id"]
        await client.post(f"{API}/auth/logout")
        await sign_up(client, "mallory@example.com")

        response = await client.post(
            f"{API}/chat",
            json={"conversation_id": conversation_id, "content": "let me in"},
        )

        assert response.status_code == 404

    async def test_another_users_conversation_cannot_be_read(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client, "ada@example.com")
        events = await stream_chat(client, "ada's private message")
        conversation_id = first(events, "start")["conversation_id"]
        await client.post(f"{API}/auth/logout")
        await sign_up(client, "mallory@example.com")

        response = await client.get(f"{API}/chat/conversations/{conversation_id}")

        assert response.status_code == 404

    async def test_a_missing_conversation_is_indistinguishable_from_a_forbidden_one(
        self, client: AsyncClient
    ) -> None:
        """Two different 404s would let anyone probe for which ids are real."""
        await sign_up(client, "ada@example.com")
        events = await stream_chat(client, "hello")
        real_id = first(events, "start")["conversation_id"]
        await client.post(f"{API}/auth/logout")
        await sign_up(client, "mallory@example.com")

        forbidden = await client.get(f"{API}/chat/conversations/{real_id}")
        missing = await client.get(
            f"{API}/chat/conversations/11111111-1111-1111-1111-111111111111"
        )

        assert forbidden.status_code == missing.status_code == 404
        assert forbidden.json() == missing.json()

    async def test_listing_shows_only_your_own_conversations(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client, "ada@example.com")
        await stream_chat(client, "ada's chat")
        await client.post(f"{API}/auth/logout")
        await sign_up(client, "mallory@example.com")

        listed = (await client.get(f"{API}/chat/conversations")).json()

        assert listed == []


class TestReadingHistory:
    async def test_returns_the_conversation_with_its_messages(
        self, client: AsyncClient
    ) -> None:
        """What a browser refresh relies on."""
        await sign_up(client)
        events = await stream_chat(client, "hello there")
        conversation_id = first(events, "start")["conversation_id"]

        detail = (
            await client.get(f"{API}/chat/conversations/{conversation_id}")
        ).json()

        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        assert detail["messages"][0]["content"] == "hello there"

    async def test_messages_come_back_in_position_order(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client)
        events = await stream_chat(client, "first")
        conversation_id = first(events, "start")["conversation_id"]
        await stream_chat(client, "second", conversation_id)

        detail = (
            await client.get(f"{API}/chat/conversations/{conversation_id}")
        ).json()

        assert [m["position"] for m in detail["messages"]] == [0, 1, 2, 3]


class TestValidation:
    async def test_rejects_an_empty_message(self, client: AsyncClient) -> None:
        await sign_up(client)

        response = await client.post(f"{API}/chat", json={"content": ""})

        assert response.status_code == 422

    async def test_rejects_an_absurdly_long_message(
        self, client: AsyncClient
    ) -> None:
        """COST: an unbounded prompt is an unbounded bill. Rejected outright
        rather than truncated -- a truncated question gets a confident answer
        to something the user did not ask."""
        await sign_up(client)

        response = await client.post(
            f"{API}/chat", json={"content": "x" * (MAX_MESSAGE_LENGTH + 1)}
        )

        assert response.status_code == 422

    async def test_rejects_a_malformed_conversation_id(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client)

        response = await client.post(
            f"{API}/chat", json={"conversation_id": "not-a-uuid", "content": "hi"}
        )

        assert response.status_code == 422


class TestRateLimiting:
    async def test_a_burst_of_messages_is_eventually_blocked(
        self, client: AsyncClient
    ) -> None:
        """SECURITY/COST: every request spends money, so an unlimited endpoint
        is a financial denial of service."""
        await sign_up(client)

        statuses = []
        for _ in range(35):
            response = await client.post(f"{API}/chat", json={"content": "hello"})
            statuses.append(response.status_code)
            await response.aclose()

        assert 429 in statuses, f"never rate limited: {set(statuses)}"

    @pytest.mark.parametrize("header", ["retry-after"])
    async def test_the_block_says_when_to_retry(
        self, client: AsyncClient, header: str
    ) -> None:
        await sign_up(client)

        blocked = None
        for _ in range(35):
            response = await client.post(f"{API}/chat", json={"content": "hello"})
            await response.aclose()
            if response.status_code == 429:
                blocked = response
                break

        assert blocked is not None
        assert header in {k.lower() for k in blocked.headers}


class _FailingProvider(ModelProvider):
    """Fails partway through, the way a real provider does mid-generation."""

    name = "failing"

    def __init__(self, error: Exception, chunks_first: int = 0) -> None:
        self._error = error
        self._chunks_first = chunks_first

    async def stream(self, messages, *, system=None):  # type: ignore[no-untyped-def]
        for index in range(self._chunks_first):
            yield TextDelta(f"partial {index} ")
        raise self._error


class TestProviderFailures:
    """A failure has to reach the user as an event *inside* the stream.

    By the time the provider fails the response has already begun, so the
    status code is long since sent -- returning a 500 is no longer an option.
    """

    async def _events_with(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        provider: ModelProvider,
    ) -> list[dict[str, Any]]:
        monkeypatch.setattr("app.api.routes.chat.get_provider", lambda: provider)
        events: list[dict[str, Any]] = []
        async with client.stream(
            "POST", f"{API}/chat", json={"content": "hello"}
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))
        return events

    async def test_a_rate_limited_provider_is_reported_as_such(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await sign_up(client)

        events = await self._events_with(
            client, monkeypatch, _FailingProvider(ModelRateLimitError("slow down"))
        )

        assert first(events, "error")["code"] == "rate_limited"

    async def test_a_missing_credential_is_not_blamed_on_the_user(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An operator problem. The message must not suggest the user did
        anything wrong, and must not hint at what is misconfigured."""
        await sign_up(client)

        events = await self._events_with(
            client, monkeypatch, _FailingProvider(ModelAuthError("bad key"))
        )

        error = first(events, "error")
        assert error["code"] == "provider_unavailable"
        assert "key" not in error["message"].lower()

    async def test_text_produced_before_a_failure_is_still_saved(
        self, client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It was generated and billed before the failure."""
        await sign_up(client)

        events = await self._events_with(
            client,
            monkeypatch,
            _FailingProvider(ModelUnavailableError("gone"), chunks_first=2),
        )

        assert first(events, "error")["code"] == "provider_unavailable"
        assistant = (
            await db.execute(
                select(Message).where(Message.role == MessageRole.ASSISTANT)
            )
        ).scalar_one()
        assert assistant.content == "partial 0 partial 1 "

    async def test_no_done_event_follows_a_failure(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A client that saw both would not know whether the turn succeeded."""
        await sign_up(client)

        events = await self._events_with(
            client, monkeypatch, _FailingProvider(ModelUnavailableError("gone"))
        )

        assert [e["type"] for e in events if e["type"] in {"done", "error"}] == ["error"]

    async def test_the_user_message_is_kept_even_when_the_reply_fails(
        self, client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise a failed turn would silently discard what the user typed."""
        await sign_up(client)

        await self._events_with(
            client, monkeypatch, _FailingProvider(ModelUnavailableError("gone"))
        )

        user_message = (
            await db.execute(select(Message).where(Message.role == MessageRole.USER))
        ).scalar_one()
        assert user_message.content == "hello"


async def regenerate(
    client: AsyncClient, conversation_id: str
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async with client.stream(
        "POST", f"{API}/chat/regenerate", json={"conversation_id": conversation_id}
    ) as response:
        assert response.status_code == 200, await response.aread()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


class TestRegenerate:
    async def test_requires_a_session(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{API}/chat/regenerate",
            json={"conversation_id": "11111111-1111-1111-1111-111111111111"},
        )

        assert response.status_code == 401

    async def test_replaces_the_previous_reply_rather_than_adding_one(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """Regenerating means the old answer is discarded. Keeping both would
        need a notion of alternative replies that nothing here has."""
        await sign_up(client)
        events = await stream_chat(client, "hello")
        conversation_id = first(events, "start")["conversation_id"]

        await regenerate(client, conversation_id)

        stored = (
            (await db.execute(select(Message).order_by(Message.position)))
            .scalars()
            .all()
        )
        assert [m.role for m in stored] == [MessageRole.USER, MessageRole.ASSISTANT]
        assert [m.position for m in stored] == [0, 1]

    async def test_the_question_is_kept(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        await sign_up(client)
        events = await stream_chat(client, "what is a token?")
        conversation_id = first(events, "start")["conversation_id"]

        await regenerate(client, conversation_id)

        user_message = (
            await db.execute(select(Message).where(Message.role == MessageRole.USER))
        ).scalar_one()
        assert user_message.content == "what is a token?"

    async def test_the_new_reply_answers_the_same_question(
        self, client: AsyncClient
    ) -> None:
        """The echo provider repeats the last user turn, so this proves the
        original question -- not an empty history -- reached the provider."""
        await sign_up(client)
        events = await stream_chat(client, "marmalade")
        conversation_id = first(events, "start")["conversation_id"]

        again = await regenerate(client, conversation_id)

        assert "marmalade" in texts(again)

    async def test_start_reports_the_question_being_answered(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client)
        events = await stream_chat(client, "hello")
        conversation_id = first(events, "start")["conversation_id"]
        original_question_id = first(events, "start")["user_message_id"]

        again = await regenerate(client, conversation_id)

        assert first(again, "start")["user_message_id"] == original_question_id

    async def test_regenerating_twice_still_leaves_one_reply(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        await sign_up(client)
        events = await stream_chat(client, "hello")
        conversation_id = first(events, "start")["conversation_id"]

        await regenerate(client, conversation_id)
        await regenerate(client, conversation_id)

        assert len((await db.execute(select(Message))).scalars().all()) == 2

    async def test_works_after_a_reply_that_produced_nothing(
        self, client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed attempt leaves the question last with no reply after it.
        There is nothing to discard, and regenerating must still work."""
        await sign_up(client)
        monkeypatch.setattr(
            "app.api.routes.chat.get_provider",
            lambda: _FailingProvider(ModelUnavailableError("gone")),
        )
        async with client.stream(
            "POST", f"{API}/chat", json={"content": "hello"}
        ) as response:
            conversation_id = None
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[len("data: ") :])
                    if event["type"] == "start":
                        conversation_id = event["conversation_id"]
        assert conversation_id is not None
        monkeypatch.undo()

        again = await regenerate(client, conversation_id)

        assert first(again, "done")
        stored = (
            (await db.execute(select(Message).order_by(Message.position)))
            .scalars()
            .all()
        )
        assert [m.role for m in stored] == [MessageRole.USER, MessageRole.ASSISTANT]

    async def test_refuses_an_empty_conversation(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        await sign_up(client)
        user = (await db.execute(select(User))).scalar_one()
        empty = await create_conversation(db, user_id=user.id, title="empty")

        response = await client.post(
            f"{API}/chat/regenerate", json={"conversation_id": str(empty.id)}
        )

        assert response.status_code == 409

    async def test_another_users_conversation_cannot_be_regenerated(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client, "ada@example.com")
        events = await stream_chat(client, "ada's question")
        conversation_id = first(events, "start")["conversation_id"]
        await client.post(f"{API}/auth/logout")
        await sign_up(client, "mallory@example.com")

        response = await client.post(
            f"{API}/chat/regenerate", json={"conversation_id": conversation_id}
        )

        assert response.status_code == 404

    async def test_is_rate_limited(self, client: AsyncClient) -> None:
        """It costs exactly what sending costs, and is the easiest thing in the
        product to sit and hammer."""
        await sign_up(client)
        events = await stream_chat(client, "hello")
        conversation_id = first(events, "start")["conversation_id"]

        statuses = []
        for _ in range(35):
            response = await client.post(
                f"{API}/chat/regenerate", json={"conversation_id": conversation_id}
            )
            statuses.append(response.status_code)
            await response.aclose()

        assert 429 in statuses, f"never rate limited: {set(statuses)}"
