"""Retrieval tests against a real PostgreSQL instance with pgvector.

**Why not SQLite like the rest of the suite.** Similarity search *is* pgvector:
the `<=>` operator, the vector column, the index. Emulating it in Python would
mean the suite passed against a reimplementation while production ran something
else -- exactly the failure a test exists to prevent. So these use a real
database and skip, loudly, when one is not reachable.

Retrieval runs on the offline hashing provider, which matches on shared
vocabulary rather than meaning (see its module docstring). The assertions are
written accordingly: they check that the *pipeline* selects the right passage,
not that the embedding model understands synonyms -- because this one does not.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import FileChunk
from app.models.file import File, FileStatus
from app.services.auth import create_user
from app.services.retrieval import index_file, search_chunks

pytestmark = pytest.mark.usefixtures("postgres_url")

PASSWORD = "a-sufficiently-long-password"


async def make_user(db: AsyncSession, email: str) -> uuid.UUID:
    user = await create_user(db, email=email, password=PASSWORD)
    return user.id


async def make_file(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    text: str,
    filename: str = "doc.txt",
) -> File:
    file = File(
        user_id=user_id,
        filename=filename,
        storage_key=uuid.uuid4().hex,
        content_type="text/plain",
        size_bytes=len(text.encode()),
        status=FileStatus.READY,
        extracted_text=text,
    )
    db.add(file)
    await db.commit()
    await db.refresh(file)
    return file


# Distinct vocabularies, so a lexical embedding can tell them apart.
SOLAR = (
    "Photovoltaic panels convert sunlight directly into electricity. "
    "A residential solar installation typically produces four kilowatts. "
    "Panel efficiency degrades slowly over a twenty five year lifespan."
)
BAKING = (
    "Sourdough bread requires a mature starter culture. "
    "Hydration ratios above eighty percent produce an open crumb. "
    "Bake at two hundred and thirty degrees with steam for the first ten minutes."
)


class TestIndexing:
    async def test_writes_chunks_for_a_text_file(self, pg_db: AsyncSession) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)

        written = await index_file(pg_db, file=file)

        assert written > 0
        stored = (await pg_db.execute(select(FileChunk))).scalars().all()
        assert len(stored) == written
        assert all(len(chunk.embedding) == 512 for chunk in stored)

    async def test_copies_the_owner_onto_every_chunk(
        self, pg_db: AsyncSession
    ) -> None:
        """The denormalised column the security filter depends on."""
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)

        await index_file(pg_db, file=file)

        stored = (await pg_db.execute(select(FileChunk))).scalars().all()
        assert all(chunk.user_id == user_id for chunk in stored)

    async def test_re_indexing_replaces_rather_than_accumulates(
        self, pg_db: AsyncSession
    ) -> None:
        """Duplicated chunks would each match a query and fill the model's
        context with the same passage twice."""
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)

        first = await index_file(pg_db, file=file)
        second = await index_file(pg_db, file=file)

        stored = (await pg_db.execute(select(FileChunk))).scalars().all()
        assert first == second == len(stored)

    @pytest.mark.parametrize(
        ("status", "text"),
        [
            (FileStatus.UNSUPPORTED, None),
            (FileStatus.EXTRACTED_EMPTY, None),
            (FileStatus.FAILED, None),
            (FileStatus.READY, ""),
        ],
    )
    async def test_indexes_nothing_when_there_is_no_text(
        self, pg_db: AsyncSession, status: FileStatus, text: str | None
    ) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text="placeholder")
        file.status = status
        file.extracted_text = text
        await pg_db.commit()

        assert await index_file(pg_db, file=file) == 0

    async def test_deleting_a_file_removes_its_chunks(
        self, pg_db: AsyncSession
    ) -> None:
        """The payoff for keeping vectors in PostgreSQL: no separate store can
        be left holding searchable copies of a deleted document."""
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)
        await index_file(pg_db, file=file)

        await pg_db.delete(file)
        await pg_db.commit()

        assert (await pg_db.execute(select(FileChunk))).scalars().all() == []


class TestSearch:
    async def test_finds_the_relevant_document(self, pg_db: AsyncSession) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        solar = await make_file(
            pg_db, user_id=user_id, text=SOLAR, filename="solar.txt"
        )
        baking = await make_file(
            pg_db, user_id=user_id, text=BAKING, filename="baking.txt"
        )
        await index_file(pg_db, file=solar)
        await index_file(pg_db, file=baking)

        found = await search_chunks(
            pg_db, query="How efficient are photovoltaic panels?", user_id=user_id
        )

        assert found
        assert found[0].filename == "solar.txt"

    async def test_orders_by_similarity(self, pg_db: AsyncSession) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        for name, text in (("solar.txt", SOLAR), ("baking.txt", BAKING)):
            file = await make_file(pg_db, user_id=user_id, text=text, filename=name)
            await index_file(pg_db, file=file)

        found = await search_chunks(
            pg_db, query="sourdough starter hydration", user_id=user_id
        )

        assert found[0].filename == "baking.txt"
        similarities = [chunk.similarity for chunk in found]
        assert similarities == sorted(similarities, reverse=True)

    async def test_respects_the_result_limit(self, pg_db: AsyncSession) -> None:
        """Every retrieved chunk is tokens paid for on every question."""
        user_id = await make_user(pg_db, "ada@example.com")
        long_text = "\n\n".join(
            f"Paragraph {index} discusses photovoltaic panels and sunlight."
            for index in range(40)
        )
        file = await make_file(pg_db, user_id=user_id, text=long_text)
        await index_file(pg_db, file=file)

        found = await search_chunks(
            pg_db, query="photovoltaic sunlight", user_id=user_id, limit=3
        )

        assert len(found) <= 3

    async def test_an_unrelated_question_returns_nothing(
        self, pg_db: AsyncSession
    ) -> None:
        """Without a similarity floor, retrieval always returns its k nearest
        chunks however irrelevant -- so asking about the weather with a tax
        return uploaded would yield five passages about tax."""
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)
        await index_file(pg_db, file=file)

        found = await search_chunks(
            pg_db,
            query="zzzz qqqq vvvv unrelated gibberish tokens",
            user_id=user_id,
        )

        assert found == []

    async def test_an_empty_query_returns_nothing(
        self, pg_db: AsyncSession
    ) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)
        await index_file(pg_db, file=file)

        assert await search_chunks(pg_db, query="   ", user_id=user_id) == []

    async def test_similarity_is_a_fraction(self, pg_db: AsyncSession) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)
        await index_file(pg_db, file=file)

        found = await search_chunks(
            pg_db, query="photovoltaic panels sunlight electricity", user_id=user_id
        )

        assert found
        assert all(0.0 <= chunk.similarity <= 1.0 for chunk in found)

    async def test_can_be_scoped_to_specific_files(
        self, pg_db: AsyncSession
    ) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        solar = await make_file(
            pg_db, user_id=user_id, text=SOLAR, filename="solar.txt"
        )
        baking = await make_file(
            pg_db, user_id=user_id, text=BAKING, filename="baking.txt"
        )
        await index_file(pg_db, file=solar)
        await index_file(pg_db, file=baking)

        found = await search_chunks(
            pg_db,
            query="panels sunlight starter hydration",
            user_id=user_id,
            file_ids=[baking.id],
        )

        assert found
        assert {chunk.filename for chunk in found} == {"baking.txt"}

    async def test_reports_where_a_passage_came_from(
        self, pg_db: AsyncSession
    ) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(
            pg_db, user_id=user_id, text=SOLAR, filename="report.txt"
        )
        await index_file(pg_db, file=file)

        found = await search_chunks(
            pg_db, query="photovoltaic panels", user_id=user_id
        )

        assert found[0].file_id == file.id
        assert found[0].filename == "report.txt"
        assert found[0].position >= 0


class TestUserIsolation:
    """The most important tests in this phase.

    Retrieval pastes stored text into a prompt. A chunk returned for the wrong
    user is a data breach delivered in readable prose.
    """

    async def test_never_returns_another_users_chunks(
        self, pg_db: AsyncSession
    ) -> None:
        ada = await make_user(pg_db, "ada@example.com")
        mallory = await make_user(pg_db, "mallory@example.com")
        ada_file = await make_file(
            pg_db, user_id=ada, text=SOLAR, filename="ada-private.txt"
        )
        await index_file(pg_db, file=ada_file)

        found = await search_chunks(
            pg_db, query="photovoltaic panels sunlight", user_id=mallory
        )

        assert found == []

    async def test_naming_another_users_file_returns_nothing(
        self, pg_db: AsyncSession
    ) -> None:
        """Scoping by file id must not grant access to it. The owner filter
        applies regardless, so naming someone else's file narrows the search
        to nothing rather than reaching into it."""
        ada = await make_user(pg_db, "ada@example.com")
        mallory = await make_user(pg_db, "mallory@example.com")
        ada_file = await make_file(pg_db, user_id=ada, text=SOLAR)
        await index_file(pg_db, file=ada_file)

        found = await search_chunks(
            pg_db,
            query="photovoltaic panels sunlight",
            user_id=mallory,
            file_ids=[ada_file.id],
        )

        assert found == []

    async def test_each_user_sees_only_their_own(
        self, pg_db: AsyncSession
    ) -> None:
        ada = await make_user(pg_db, "ada@example.com")
        mallory = await make_user(pg_db, "mallory@example.com")
        for owner, name in ((ada, "ada.txt"), (mallory, "mallory.txt")):
            file = await make_file(
                pg_db, user_id=owner, text=SOLAR, filename=name
            )
            await index_file(pg_db, file=file)

        ada_found = await search_chunks(
            pg_db, query="photovoltaic panels", user_id=ada
        )
        mallory_found = await search_chunks(
            pg_db, query="photovoltaic panels", user_id=mallory
        )

        assert {chunk.filename for chunk in ada_found} == {"ada.txt"}
        assert {chunk.filename for chunk in mallory_found} == {"mallory.txt"}

    async def test_deleting_a_user_removes_their_chunks(
        self, pg_db: AsyncSession
    ) -> None:
        from app.models.user import User

        ada = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=ada, text=SOLAR)
        await index_file(pg_db, file=file)

        user = await pg_db.get(User, ada)
        assert user is not None
        await pg_db.delete(user)
        await pg_db.commit()

        assert (await pg_db.execute(select(FileChunk))).scalars().all() == []
