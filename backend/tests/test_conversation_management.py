"""Tests for Phase 4: renaming, deleting, searching, paging and editing.

Kept apart from `test_chat_api.py`, which covers sending and streaming. These
exercise what happens to a conversation *after* it exists.

The offline echo provider answers throughout, so nothing here calls a paid API.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import TITLE_MAX_LENGTH, Conversation
from app.models.message import Message, MessageRole
from app.schemas.chat import MAX_MESSAGE_LENGTH

API = "/api/v1"
PASSWORD = "a-sufficiently-long-password"


# --- helpers --------------------------------------------------------------


async def sign_up(client: AsyncClient, email: str = "ada@example.com") -> None:
    response = await client.post(
        f"{API}/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201


async def stream_chat(
    client: AsyncClient, content: str, conversation_id: str | None = None
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"content": content}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id

    events: list[dict[str, Any]] = []
    async with client.stream("POST", f"{API}/chat", json=payload) as response:
        assert response.status_code == 200, await response.aread()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def first(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    return next(event for event in events if event["type"] == event_type)


def texts(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "delta")


async def new_conversation(client: AsyncClient, first_message: str) -> str:
    """Start a conversation and return its id."""
    return first(await stream_chat(client, first_message), "start")["conversation_id"]


async def list_page(client: AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get(f"{API}/chat/conversations", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def titles(page: dict[str, Any]) -> list[str | None]:
    return [item["title"] for item in page["items"]]


async def edit_message(
    client: AsyncClient, conversation_id: str, message_id: str, content: str
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async with client.stream(
        "POST",
        f"{API}/chat/edit",
        json={
            "conversation_id": conversation_id,
            "message_id": message_id,
            "content": content,
        },
    ) as response:
        assert response.status_code == 200, await response.aread()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


# --- rename ---------------------------------------------------------------


class TestRename:
    async def test_requires_a_session(self, client: AsyncClient) -> None:
        response = await client.patch(
            f"{API}/chat/conversations/11111111-1111-1111-1111-111111111111",
            json={"title": "anything"},
        )

        assert response.status_code == 401

    async def test_sets_the_new_title(self, client: AsyncClient) -> None:
        await sign_up(client)
        conversation_id = await new_conversation(client, "hello")

        response = await client.patch(
            f"{API}/chat/conversations/{conversation_id}",
            json={"title": "Embeddings research"},
        )

        assert response.status_code == 200
        assert response.json()["title"] == "Embeddings research"
        assert titles(await list_page(client)) == ["Embeddings research"]

    async def test_collapses_whitespace(self, client: AsyncClient) -> None:
        """A pasted multi-line block must not become a title containing
        newlines -- the same rule generated titles follow."""
        await sign_up(client)
        conversation_id = await new_conversation(client, "hello")

        response = await client.patch(
            f"{API}/chat/conversations/{conversation_id}",
            json={"title": "  Research\n\n  notes  "},
        )

        assert response.json()["title"] == "Research notes"

    @pytest.mark.parametrize("title", ["", "   ", "\n\t "])
    async def test_rejects_a_blank_title(self, client: AsyncClient, title: str) -> None:
        """A whitespace-only title renders as an invisible sidebar row."""
        await sign_up(client)
        conversation_id = await new_conversation(client, "hello")

        response = await client.patch(
            f"{API}/chat/conversations/{conversation_id}", json={"title": title}
        )

        assert response.status_code == 422

    async def test_rejects_a_title_over_the_column_limit(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client)
        conversation_id = await new_conversation(client, "hello")

        response = await client.patch(
            f"{API}/chat/conversations/{conversation_id}",
            json={"title": "x" * (TITLE_MAX_LENGTH + 1)},
        )

        assert response.status_code == 422

    async def test_does_not_reorder_the_sidebar(self, client: AsyncClient) -> None:
        """Renaming is not activity. A thread you merely retitled must not jump
        above one you actually used more recently."""
        await sign_up(client)
        older = await new_conversation(client, "older thread")
        await new_conversation(client, "newer thread")

        await client.patch(
            f"{API}/chat/conversations/{older}", json={"title": "Renamed"}
        )

        assert titles(await list_page(client)) == ["newer thread", "Renamed"]

    async def test_another_users_conversation_cannot_be_renamed(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client, "ada@example.com")
        conversation_id = await new_conversation(client, "ada's thread")
        await client.post(f"{API}/auth/logout")
        await sign_up(client, "mallory@example.com")

        response = await client.patch(
            f"{API}/chat/conversations/{conversation_id}", json={"title": "mine now"}
        )

        assert response.status_code == 404


# --- delete ---------------------------------------------------------------


class TestDelete:
    async def test_requires_a_session(self, client: AsyncClient) -> None:
        response = await client.delete(
            f"{API}/chat/conversations/11111111-1111-1111-1111-111111111111"
        )

        assert response.status_code == 401

    async def test_removes_the_conversation_and_its_messages(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """The messages must go too. A cascade that silently failed would leave
        orphaned rows holding the user's text after they deleted it."""
        await sign_up(client)
        conversation_id = await new_conversation(client, "hello")

        response = await client.delete(f"{API}/chat/conversations/{conversation_id}")

        assert response.status_code == 204
        assert titles(await list_page(client)) == []
        assert (await db.execute(select(Message))).scalars().all() == []

    async def test_leaves_other_conversations_alone(self, client: AsyncClient) -> None:
        await sign_up(client)
        doomed = await new_conversation(client, "delete me")
        await new_conversation(client, "keep me")

        await client.delete(f"{API}/chat/conversations/{doomed}")

        assert titles(await list_page(client)) == ["keep me"]

    async def test_deleting_twice_is_a_404(self, client: AsyncClient) -> None:
        await sign_up(client)
        conversation_id = await new_conversation(client, "hello")

        await client.delete(f"{API}/chat/conversations/{conversation_id}")
        again = await client.delete(f"{API}/chat/conversations/{conversation_id}")

        assert again.status_code == 404

    async def test_another_users_conversation_cannot_be_deleted(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        await sign_up(client, "ada@example.com")
        conversation_id = await new_conversation(client, "ada's thread")
        await client.post(f"{API}/auth/logout")
        await sign_up(client, "mallory@example.com")

        response = await client.delete(f"{API}/chat/conversations/{conversation_id}")

        assert response.status_code == 404
        assert len((await db.execute(select(Conversation))).scalars().all()) == 1


# --- search ---------------------------------------------------------------


class TestSearch:
    async def test_matches_on_title(self, client: AsyncClient) -> None:
        await sign_up(client)
        await new_conversation(client, "How do embeddings work?")
        await new_conversation(client, "Explain async python")

        assert titles(await list_page(client, q="embeddings")) == [
            "How do embeddings work?"
        ]

    async def test_matches_on_message_text_not_just_the_title(
        self, client: AsyncClient
    ) -> None:
        """The title comes from the first message, so a term used later in a
        thread appears in no title at all. Title-only search would miss it."""
        await sign_up(client)
        conversation_id = await new_conversation(client, "a generic opening")
        await stream_chat(client, "what about pgvector?", conversation_id)

        assert titles(await list_page(client, q="pgvector")) == ["a generic opening"]

    async def test_is_case_insensitive(self, client: AsyncClient) -> None:
        await sign_up(client)
        await new_conversation(client, "Postgres tuning")

        assert titles(await list_page(client, q="POSTGRES")) == ["Postgres tuning"]

    async def test_returns_a_thread_once_however_many_times_it_matches(
        self, client: AsyncClient
    ) -> None:
        """A JOIN would return this conversation once per matching message."""
        await sign_up(client)
        conversation_id = await new_conversation(client, "pgvector one")
        await stream_chat(client, "pgvector two", conversation_id)
        await stream_chat(client, "pgvector three", conversation_id)

        assert len((await list_page(client, q="pgvector"))["items"]) == 1

    async def test_a_percent_sign_is_not_a_wildcard(self, client: AsyncClient) -> None:
        """Unescaped, `%` in a LIKE pattern matches everything -- so searching
        for a literal percentage would return every conversation."""
        await sign_up(client)
        await new_conversation(client, "growth was 100% last year")
        await new_conversation(client, "something unrelated")

        assert titles(await list_page(client, q="100%")) == [
            "growth was 100% last year"
        ]

    async def test_an_underscore_is_not_a_wildcard(self, client: AsyncClient) -> None:
        await sign_up(client)
        await new_conversation(client, "call get_user now")
        await new_conversation(client, "call getXuser now")

        assert titles(await list_page(client, q="get_user")) == ["call get_user now"]

    async def test_a_blank_search_returns_everything(
        self, client: AsyncClient
    ) -> None:
        """Whitespace is not a search term. Treating it as one would scan for
        `%  %` and return nothing, which reads as a broken search box."""
        await sign_up(client)
        await new_conversation(client, "hello")

        assert len((await list_page(client, q="   "))["items"]) == 1

    async def test_no_match_is_an_empty_page_not_an_error(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client)
        await new_conversation(client, "hello")

        page = await list_page(client, q="nothing matches this")

        assert page["items"] == []
        assert page["has_more"] is False

    async def test_search_cannot_reach_another_users_conversations(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client, "ada@example.com")
        await new_conversation(client, "ada's secret project")
        await client.post(f"{API}/auth/logout")
        await sign_up(client, "mallory@example.com")

        assert titles(await list_page(client, q="secret")) == []

    async def test_rejects_an_overlong_term(self, client: AsyncClient) -> None:
        await sign_up(client)

        response = await client.get(
            f"{API}/chat/conversations", params={"q": "x" * 201}
        )

        assert response.status_code == 422


# --- pagination -----------------------------------------------------------


class TestPagination:
    async def test_returns_only_the_requested_page_size(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client)
        for index in range(3):
            await new_conversation(client, f"thread {index}")

        page = await list_page(client, limit=2)

        assert len(page["items"]) == 2
        assert page["has_more"] is True

    async def test_the_last_page_reports_no_more(self, client: AsyncClient) -> None:
        await sign_up(client)
        for index in range(3):
            await new_conversation(client, f"thread {index}")

        page = await list_page(client, limit=2, offset=2)

        assert len(page["items"]) == 1
        assert page["has_more"] is False

    async def test_pages_do_not_overlap_or_skip(self, client: AsyncClient) -> None:
        await sign_up(client)
        for index in range(5):
            await new_conversation(client, f"thread {index}")

        seen: list[str | None] = []
        for offset in (0, 2, 4):
            seen += titles(await list_page(client, limit=2, offset=offset))

        assert sorted(str(title) for title in seen) == [
            f"thread {index}" for index in range(5)
        ]

    async def test_search_and_pagination_combine(self, client: AsyncClient) -> None:
        await sign_up(client)
        for index in range(3):
            await new_conversation(client, f"pgvector {index}")
        await new_conversation(client, "unrelated")

        page = await list_page(client, q="pgvector", limit=2)

        assert len(page["items"]) == 2
        assert page["has_more"] is True
        assert all("pgvector" in (title or "") for title in titles(page))

    @pytest.mark.parametrize(
        ("params", "reason"),
        [
            ({"limit": 0}, "zero is not a page"),
            ({"limit": 101}, "above the ceiling"),
            ({"limit": -1}, "negative limit"),
            ({"offset": -1}, "negative offset"),
        ],
    )
    async def test_rejects_out_of_range_paging(
        self, client: AsyncClient, params: dict[str, int], reason: str
    ) -> None:
        """The cap is enforced by the route, not trusted from the client."""
        await sign_up(client)

        response = await client.get(f"{API}/chat/conversations", params=params)

        assert response.status_code == 422, reason


# --- editing a question ---------------------------------------------------


class TestEditMessage:
    async def test_requires_a_session(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{API}/chat/edit",
            json={
                "conversation_id": "11111111-1111-1111-1111-111111111111",
                "message_id": "22222222-2222-2222-2222-222222222222",
                "content": "hello",
            },
        )

        assert response.status_code == 401

    async def test_replaces_the_question_text(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        await sign_up(client)
        events = await stream_chat(client, "what is a tokne?")
        start = first(events, "start")

        await edit_message(
            client, start["conversation_id"], start["user_message_id"], "what is a token?"
        )

        question = (
            await db.execute(select(Message).where(Message.role == MessageRole.USER))
        ).scalar_one()
        assert question.content == "what is a token?"

    async def test_discards_every_turn_after_the_edited_one(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """Turns after the edit were answering a question that was never asked.
        Keeping them would show a transcript that is simply untrue."""
        await sign_up(client)
        events = await stream_chat(client, "first question")
        start = first(events, "start")
        conversation_id = start["conversation_id"]
        await stream_chat(client, "second question", conversation_id)
        await stream_chat(client, "third question", conversation_id)

        await edit_message(
            client, conversation_id, start["user_message_id"], "first question, reworded"
        )

        stored = (
            (await db.execute(select(Message).order_by(Message.position)))
            .scalars()
            .all()
        )
        assert [(m.role, m.position) for m in stored] == [
            (MessageRole.USER, 0),
            (MessageRole.ASSISTANT, 1),
        ]
        assert stored[0].content == "first question, reworded"

    async def test_the_new_reply_answers_the_new_wording(
        self, client: AsyncClient
    ) -> None:
        """The echo provider repeats the last user turn, so this proves the
        edited text -- not the original -- reached the provider."""
        await sign_up(client)
        events = await stream_chat(client, "marmalade")
        start = first(events, "start")

        again = await edit_message(
            client, start["conversation_id"], start["user_message_id"], "chutney"
        )

        assert "chutney" in texts(again)
        assert "marmalade" not in texts(again)

    async def test_retitles_the_conversation_when_the_first_message_changes(
        self, client: AsyncClient
    ) -> None:
        """The title was derived from that message and would otherwise keep
        advertising a question no longer in the thread."""
        await sign_up(client)
        events = await stream_chat(client, "How do embedings work?")
        start = first(events, "start")

        await edit_message(
            client,
            start["conversation_id"],
            start["user_message_id"],
            "How do embeddings work?",
        )

        assert titles(await list_page(client)) == ["How do embeddings work?"]

    async def test_editing_a_later_message_keeps_the_title(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client)
        conversation_id = await new_conversation(client, "Original title")
        events = await stream_chat(client, "a follow up", conversation_id)
        second_id = first(events, "start")["user_message_id"]

        await edit_message(client, conversation_id, second_id, "a different follow up")

        assert titles(await list_page(client)) == ["Original title"]

    async def test_an_assistant_message_cannot_be_edited(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """SECURITY: rewriting a reply would let the caller put words in the
        model's mouth, and those words come back as context on every later
        turn -- self-inflicted prompt injection with a store behind it."""
        await sign_up(client)
        conversation_id = await new_conversation(client, "hello")
        reply = (
            await db.execute(
                select(Message).where(Message.role == MessageRole.ASSISTANT)
            )
        ).scalar_one()

        response = await client.post(
            f"{API}/chat/edit",
            json={
                "conversation_id": conversation_id,
                "message_id": str(reply.id),
                "content": "I definitely said this",
            },
        )

        assert response.status_code == 409

    async def test_a_message_from_another_conversation_is_not_found(
        self, client: AsyncClient
    ) -> None:
        """A message id is not self-authorizing: it is checked against the
        conversation the caller was actually authorized for."""
        await sign_up(client)
        other_message_id = first(await stream_chat(client, "thread one"), "start")[
            "user_message_id"
        ]
        second_conversation = await new_conversation(client, "thread two")

        response = await client.post(
            f"{API}/chat/edit",
            json={
                "conversation_id": second_conversation,
                "message_id": other_message_id,
                "content": "hello",
            },
        )

        assert response.status_code == 404

    async def test_another_users_message_cannot_be_edited(
        self, client: AsyncClient
    ) -> None:
        await sign_up(client, "ada@example.com")
        start = first(await stream_chat(client, "ada's question"), "start")
        await client.post(f"{API}/auth/logout")
        await sign_up(client, "mallory@example.com")

        response = await client.post(
            f"{API}/chat/edit",
            json={
                "conversation_id": start["conversation_id"],
                "message_id": start["user_message_id"],
                "content": "rewritten",
            },
        )

        assert response.status_code == 404

    @pytest.mark.parametrize("content", ["", "x" * (MAX_MESSAGE_LENGTH + 1)])
    async def test_rejects_content_outside_the_limits(
        self, client: AsyncClient, content: str
    ) -> None:
        await sign_up(client)
        start = first(await stream_chat(client, "hello"), "start")

        response = await client.post(
            f"{API}/chat/edit",
            json={
                "conversation_id": start["conversation_id"],
                "message_id": start["user_message_id"],
                "content": content,
            },
        )

        assert response.status_code == 422

    async def test_is_rate_limited(self, client: AsyncClient) -> None:
        await sign_up(client)
        start = first(await stream_chat(client, "hello"), "start")

        statuses = []
        for index in range(35):
            response = await client.post(
                f"{API}/chat/edit",
                json={
                    "conversation_id": start["conversation_id"],
                    "message_id": start["user_message_id"],
                    "content": f"attempt {index}",
                },
            )
            statuses.append(response.status_code)
            await response.aclose()

        assert 429 in statuses, f"never rate limited: {set(statuses)}"

    async def test_sending_and_regenerating_share_one_quota(
        self, client: AsyncClient
    ) -> None:
        """Separate counters would let a caller spend several times the
        intended budget by rotating between endpoints."""
        await sign_up(client)
        conversation_id = await new_conversation(client, "hello")

        statuses = []
        for _ in range(20):
            for path, body in (
                ("/chat/regenerate", {"conversation_id": conversation_id}),
                ("/chat", {"conversation_id": conversation_id, "content": "again"}),
            ):
                response = await client.post(f"{API}{path}", json=body)
                statuses.append(response.status_code)
                await response.aclose()

        assert 429 in statuses, f"never rate limited: {set(statuses)}"
