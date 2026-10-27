"""Files added to a chat: attached, remembered, and answered from first.

Against real PostgreSQL, because what matters is retrieval. The key case is the
one that motivated this: a question that shares almost no words with the
document ("what is my email?" against a resume) must still be answered from
the file the person put in the chat, while the same question in a chat without
the file must not drag it in.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_file import ConversationFile
from tests.test_chat_with_documents import API, SOLAR_DOC, ask, sign_up, sources, upload

pytestmark = pytest.mark.usefixtures("postgres_url")

RESUME = (
    b"Ada Lovelace\n\n"
    b"Contact: ada.lovelace@example.com, +44 20 7946 0000\n\n"
    b"Analyst at the Analytical Engine Company, 1842 to 1852.\n\n"
    b"Wrote the first published algorithm intended for a machine."
)
# Shares no meaningful word with RESUME, so the lexical test embedding scores it
# below the general floor -- exactly the situation a real model faces with
# "what is my email?".
VAGUE_QUESTION = "what should I know here?"


def conversation_id(events: list[dict[str, Any]]) -> str:
    return next(event["conversation_id"] for event in events if event["type"] == "start")


def source_files(events: list[dict[str, Any]]) -> set[str]:
    return {source["filename"] for source in sources(events)}


class TestAttaching:
    async def test_a_vague_question_without_the_file_attached_finds_nothing(
        self, pg_client: AsyncClient
    ) -> None:
        """The baseline: general search respects its floor."""
        await sign_up(pg_client)
        await upload(pg_client, RESUME, "resume.txt")

        events = await ask(pg_client, VAGUE_QUESTION)

        assert sources(events) == []

    async def test_the_same_question_is_answered_from_an_attached_file(
        self, pg_client: AsyncClient
    ) -> None:
        await sign_up(pg_client)
        resume = await upload(pg_client, RESUME, "resume.txt")

        events = await ask(pg_client, VAGUE_QUESTION, attach_file_ids=[resume["id"]])

        assert source_files(events) == {"resume.txt"}

    async def test_attachment_is_remembered_for_later_questions(
        self, pg_client: AsyncClient
    ) -> None:
        await sign_up(pg_client)
        resume = await upload(pg_client, RESUME, "resume.txt")
        first = await ask(pg_client, "hello", attach_file_ids=[resume["id"]])

        later = await ask(
            pg_client, VAGUE_QUESTION, conversation_id=conversation_id(first)
        )

        assert source_files(later) == {"resume.txt"}

    async def test_another_chat_does_not_see_the_file(
        self, pg_client: AsyncClient
    ) -> None:
        await sign_up(pg_client)
        resume = await upload(pg_client, RESUME, "resume.txt")
        await ask(pg_client, "hello", attach_file_ids=[resume["id"]])

        elsewhere = await ask(pg_client, VAGUE_QUESTION)

        assert sources(elsewhere) == []

    async def test_regenerate_uses_the_chats_files(
        self, pg_client: AsyncClient
    ) -> None:
        await sign_up(pg_client)
        resume = await upload(pg_client, RESUME, "resume.txt")
        first = await ask(pg_client, VAGUE_QUESTION, attach_file_ids=[resume["id"]])

        events = []
        async with pg_client.stream(
            "POST",
            f"{API}/chat/regenerate",
            json={"conversation_id": conversation_id(first)},
        ) as response:
            import json

            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

        assert source_files(events) == {"resume.txt"}

    async def test_attached_files_come_first_other_documents_still_follow(
        self, pg_client: AsyncClient
    ) -> None:
        await sign_up(pg_client)
        resume = await upload(pg_client, RESUME, "resume.txt")
        await upload(pg_client, SOLAR_DOC, "solar.txt")

        events = await ask(
            pg_client,
            "How much do photovoltaic panels produce?",
            attach_file_ids=[resume["id"]],
        )

        names = [source["filename"] for source in sources(events)]
        assert names[0] == "resume.txt"
        assert "solar.txt" in names

    async def test_attaching_twice_stores_one_link(
        self, pg_client: AsyncClient, pg_db: AsyncSession
    ) -> None:
        await sign_up(pg_client)
        resume = await upload(pg_client, RESUME, "resume.txt")
        first = await ask(pg_client, "hello", attach_file_ids=[resume["id"]])
        await ask(
            pg_client,
            "again",
            conversation_id=conversation_id(first),
            attach_file_ids=[resume["id"]],
        )

        links = (await pg_db.execute(select(ConversationFile))).scalars().all()
        assert len(links) == 1


class TestConversationDetail:
    async def test_lists_the_chats_files(self, pg_client: AsyncClient) -> None:
        await sign_up(pg_client)
        resume = await upload(pg_client, RESUME, "resume.txt")
        first = await ask(pg_client, "hello", attach_file_ids=[resume["id"]])

        detail = (
            await pg_client.get(f"{API}/chat/conversations/{conversation_id(first)}")
        ).json()

        assert [(f["id"], f["filename"], f["has_text"]) for f in detail["files"]] == [
            (resume["id"], "resume.txt", True)
        ]

    async def test_removing_a_file_from_the_chat_keeps_the_file(
        self, pg_client: AsyncClient
    ) -> None:
        await sign_up(pg_client)
        resume = await upload(pg_client, RESUME, "resume.txt")
        first = await ask(pg_client, "hello", attach_file_ids=[resume["id"]])
        chat = conversation_id(first)

        removed = await pg_client.delete(
            f"{API}/chat/conversations/{chat}/files/{resume['id']}"
        )

        assert removed.status_code == 204
        detail = (await pg_client.get(f"{API}/chat/conversations/{chat}")).json()
        assert detail["files"] == []
        assert (await pg_client.get(f"{API}/files/{resume['id']}")).status_code == 200
        later = await ask(pg_client, VAGUE_QUESTION, conversation_id=chat)
        assert sources(later) == []

    async def test_removing_a_file_that_is_not_attached_is_a_404(
        self, pg_client: AsyncClient
    ) -> None:
        await sign_up(pg_client)
        first = await ask(pg_client, "hello")

        response = await pg_client.delete(
            f"{API}/chat/conversations/{conversation_id(first)}/files/{uuid.uuid4()}"
        )

        assert response.status_code == 404

    async def test_deleting_the_file_removes_it_from_the_chat(
        self, pg_client: AsyncClient
    ) -> None:
        await sign_up(pg_client)
        resume = await upload(pg_client, RESUME, "resume.txt")
        first = await ask(pg_client, "hello", attach_file_ids=[resume["id"]])

        await pg_client.delete(f"{API}/files/{resume['id']}")

        detail = (
            await pg_client.get(f"{API}/chat/conversations/{conversation_id(first)}")
        ).json()
        assert detail["files"] == []


class TestIsolation:
    async def test_another_users_file_cannot_be_attached(
        self, pg_client: AsyncClient, pg_db: AsyncSession
    ) -> None:
        """SECURITY: naming someone else's file id must attach nothing and
        retrieve nothing."""
        await sign_up(pg_client, "ada@example.com")
        ada_file = await upload(pg_client, RESUME, "resume.txt")
        await pg_client.post(f"{API}/auth/logout")
        await sign_up(pg_client, "mallory@example.com")

        events = await ask(pg_client, VAGUE_QUESTION, attach_file_ids=[ada_file["id"]])

        assert sources(events) == []
        assert (await pg_db.execute(select(ConversationFile))).scalars().all() == []

    async def test_another_users_chat_files_cannot_be_removed(
        self, pg_client: AsyncClient
    ) -> None:
        await sign_up(pg_client, "ada@example.com")
        resume = await upload(pg_client, RESUME, "resume.txt")
        chat = conversation_id(await ask(pg_client, "hi", attach_file_ids=[resume["id"]]))
        await pg_client.post(f"{API}/auth/logout")
        await sign_up(pg_client, "mallory@example.com")

        response = await pg_client.delete(
            f"{API}/chat/conversations/{chat}/files/{resume['id']}"
        )

        assert response.status_code == 404

    async def test_too_many_attachments_are_refused(self, pg_client: AsyncClient) -> None:
        await sign_up(pg_client)

        response = await pg_client.post(
            f"{API}/chat",
            json={"content": "hi", "attach_file_ids": [str(uuid.uuid4()) for _ in range(21)]},
        )

        assert response.status_code == 422
