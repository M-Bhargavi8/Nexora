"""Repeated passages in search, and the content-hash backfill, against real
PostgreSQL.

Duplicate *uploads* are prevented at the API (tests/test_files_api.py). These
cover what that cannot: files already stored twice before hashing existed, and
the same passage appearing in two different files.
"""

from __future__ import annotations

import hashlib
import io
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.file import File, FileStatus
from app.services import storage
from app.services.auth import create_user
from app.services.retrieval import index_file, search_chunks
from scripts import backfill_file_hashes

pytestmark = pytest.mark.usefixtures("postgres_url")

SOLAR = (
    "Photovoltaic panels convert sunlight directly into electricity. "
    "A residential solar installation typically produces four kilowatts."
)
BAKING = (
    "Sourdough bread requires a mature starter culture. "
    "Bake at two hundred and thirty degrees with steam."
)


async def make_user(db: AsyncSession, email: str = "ada@example.com") -> uuid.UUID:
    user = await create_user(db, email=email, password="a-sufficiently-long-password")
    return user.id


async def make_file(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    text: str,
    filename: str = "doc.txt",
    with_bytes: bool = False,
) -> File:
    """A READY text file. `with_bytes` also writes it to storage, unhashed --
    the state every file uploaded before migration 0006 is in."""
    if with_bytes:
        storage_key, *_ = await storage.save_upload(
            io.BytesIO(text.encode()), max_bytes=1_000_000
        )
    else:
        storage_key = uuid.uuid4().hex
    file = File(
        user_id=user_id,
        filename=filename,
        storage_key=storage_key,
        content_type="text/plain",
        size_bytes=len(text.encode()),
        status=FileStatus.READY,
        extracted_text=text,
    )
    db.add(file)
    await db.commit()
    await db.refresh(file)
    return file


class TestSearchDropsRepeats:
    async def test_two_copies_of_a_passage_are_returned_once(
        self, pg_db: AsyncSession
    ) -> None:
        user_id = await make_user(pg_db)
        for name in ("a.txt", "b.txt", "c.txt"):
            await index_file(
                pg_db, file=await make_file(pg_db, user_id=user_id, text=SOLAR, filename=name)
            )

        found = await search_chunks(pg_db, query="photovoltaic panels", user_id=user_id)

        assert len(found) == 1
        # The closest copy wins; which file it names is incidental.
        assert found[0].content == SOLAR

    async def test_repeats_do_not_crowd_out_other_passages(
        self, pg_db: AsyncSession
    ) -> None:
        """With three copies of one passage and limit 2, the second slot must go
        to the next *distinct* passage, not to a repeat."""
        user_id = await make_user(pg_db)
        for name in ("a.txt", "b.txt", "c.txt"):
            await index_file(
                pg_db, file=await make_file(pg_db, user_id=user_id, text=SOLAR, filename=name)
            )
        await index_file(
            pg_db, file=await make_file(pg_db, user_id=user_id, text=BAKING, filename="bread.txt")
        )

        found = await search_chunks(
            pg_db,
            query="photovoltaic panels and sourdough bread",
            user_id=user_id,
            limit=2,
            min_similarity=0.0,
        )

        assert sorted(chunk.content for chunk in found) == sorted([SOLAR, BAKING])

    async def test_whitespace_differences_still_count_as_a_repeat(
        self, pg_db: AsyncSession
    ) -> None:
        user_id = await make_user(pg_db)
        await index_file(pg_db, file=await make_file(pg_db, user_id=user_id, text=SOLAR))
        spaced = SOLAR.replace(". ", ".   ")
        await index_file(pg_db, file=await make_file(pg_db, user_id=user_id, text=spaced))

        found = await search_chunks(pg_db, query="photovoltaic panels", user_id=user_id)

        assert len(found) == 1


class TestHashBackfill:
    async def test_hashes_files_that_have_none(
        self,
        pg_db: AsyncSession,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await make_user(pg_db)
        file = await make_file(pg_db, user_id=user_id, text=SOLAR, with_bytes=True)

        assert await backfill_file_hashes.run(session_factory=pg_session_factory) == (1, 0)

        await pg_db.refresh(file)
        assert file.content_sha256 == hashlib.sha256(SOLAR.encode()).hexdigest()

    async def test_dry_run_changes_nothing(
        self,
        pg_db: AsyncSession,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await make_user(pg_db)
        file = await make_file(pg_db, user_id=user_id, text=SOLAR, with_bytes=True)

        await backfill_file_hashes.run(dry_run=True, session_factory=pg_session_factory)

        await pg_db.refresh(file)
        assert file.content_sha256 is None

    async def test_is_idempotent(
        self,
        pg_db: AsyncSession,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await make_user(pg_db)
        await make_file(pg_db, user_id=user_id, text=SOLAR, with_bytes=True)

        await backfill_file_hashes.run(session_factory=pg_session_factory)
        assert await backfill_file_hashes.run(session_factory=pg_session_factory) == (0, 0)

    async def test_reports_missing_bytes_and_carries_on(
        self,
        pg_db: AsyncSession,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await make_user(pg_db)
        await make_file(pg_db, user_id=user_id, text=SOLAR)  # no bytes on disk
        await make_file(pg_db, user_id=user_id, text=BAKING, with_bytes=True)

        assert await backfill_file_hashes.run(session_factory=pg_session_factory) == (1, 1)

    async def test_reports_but_never_deletes_existing_duplicates(
        self,
        pg_db: AsyncSession,
        pg_session_factory: async_sessionmaker[AsyncSession],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Which copy to keep is the owner's call, not a script's."""
        user_id = await make_user(pg_db)
        for name in ("one.txt", "two.txt"):
            await make_file(pg_db, user_id=user_id, text=SOLAR, filename=name, with_bytes=True)

        await backfill_file_hashes.run(session_factory=pg_session_factory)

        assert "Duplicate groups (same user, same bytes): 1" in capsys.readouterr().out
        assert len((await pg_db.execute(select(File))).scalars().all()) == 2

    async def test_identical_files_of_different_users_are_not_a_duplicate(
        self,
        pg_db: AsyncSession,
        pg_session_factory: async_sessionmaker[AsyncSession],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        ada = await make_user(pg_db, "ada@example.com")
        bob = await make_user(pg_db, "bob@example.com")
        await make_file(pg_db, user_id=ada, text=SOLAR, with_bytes=True)
        await make_file(pg_db, user_id=bob, text=SOLAR, with_bytes=True)

        await backfill_file_hashes.run(session_factory=pg_session_factory)

        assert "Duplicate groups (same user, same bytes): 0" in capsys.readouterr().out
