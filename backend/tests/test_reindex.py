"""Embedding-model identity, re-indexing and backfill, against real PostgreSQL.

What these prove:

* every chunk records the model that embedded it;
* a search never compares a query against another model's vectors;
* files with no chunks, or chunks from another model, are found by the
  backfill query -- and fully indexed files are not;
* `scripts.reindex` is resumable and idempotent, changes nothing on
  `--dry-run`, and leaves a file's old chunks intact when re-embedding fails.

Model switches are simulated with offline providers whose only difference is
their identity, so nothing here calls a network or spends money.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.base import EmbeddingProvider, EmbeddingPurpose, ModelUnavailableError
from app.ai.providers.hashing_embeddings import HashingEmbeddingProvider
from app.models.chunk import EMBEDDING_DIMENSIONS, FileChunk
from app.models.file import File, FileStatus
from app.services.auth import create_user
from app.services.retrieval import files_needing_index, index_file, search_chunks
from scripts import reindex

pytestmark = pytest.mark.usefixtures("postgres_url")

SOLAR = (
    "Photovoltaic panels convert sunlight directly into electricity. "
    "A residential solar installation typically produces four kilowatts."
)
BAKING = (
    "Sourdough bread requires a mature starter culture. "
    "Bake at two hundred and thirty degrees with steam."
)


class NewerHashing(HashingEmbeddingProvider):
    """Identical vectors, different identity: stands in for a model upgrade."""

    model = "hashing-v2"


class BrokenProvider(HashingEmbeddingProvider):
    """A provider that is down."""

    model = "broken-v1"

    async def embed(
        self, texts: Sequence[str], *, purpose: EmbeddingPurpose = "document"
    ) -> list[list[float]]:
        raise ModelUnavailableError("Embedding provider is unreachable")


class ConceptProvider(EmbeddingProvider):
    """A toy *semantic* model: words map to concepts, not to themselves.

    "car" and "automobile" land on the same axis, which no lexical embedding
    can do. Proves the pipeline retrieves on meaning when the provider offers
    it, and that the query side is embedded as a query.
    """

    name = "concept"
    model = "toy-v1"
    dimensions = EMBEDDING_DIMENSIONS
    CONCEPTS = {
        "car": 0, "automobile": 0, "vehicle": 0,
        "repair": 1, "mechanic": 1, "fix": 1,
        "bread": 2, "sourdough": 2, "loaf": 2,
    }

    def __init__(self) -> None:
        self.purposes: list[str] = []

    async def embed(
        self, texts: Sequence[str], *, purpose: EmbeddingPurpose = "document"
    ) -> list[list[float]]:
        self.purposes.append(purpose)
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for word in re.findall(r"[a-z]+", text.lower()):
                if word in self.CONCEPTS:
                    vector[self.CONCEPTS[word]] += 1.0
            vectors.append(vector)
        return vectors


def use_provider(monkeypatch: pytest.MonkeyPatch, provider: EmbeddingProvider) -> None:
    monkeypatch.setattr("app.services.retrieval.get_embedding_provider", lambda: provider)
    monkeypatch.setattr("scripts.reindex.get_embedding_provider", lambda: provider)


async def make_user(db: AsyncSession, email: str) -> uuid.UUID:
    user = await create_user(db, email=email, password="a-sufficiently-long-password")
    return user.id


async def make_file(
    db: AsyncSession, *, user_id: uuid.UUID, text: str, filename: str = "doc.txt"
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


async def chunk_models(db: AsyncSession) -> set[str]:
    return set((await db.execute(select(FileChunk.embedding_model))).scalars().all())


class TestModelIdentity:
    async def test_every_chunk_records_its_model(self, pg_db: AsyncSession) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)

        await index_file(pg_db, file=file)

        assert await chunk_models(pg_db) == {"hashing:hashing-v1:512"}

    async def test_search_ignores_another_models_vectors(
        self, pg_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The core guarantee. The vectors here are numerically identical, so
        a search that ignored the label *would* match -- and must not."""
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)
        await index_file(pg_db, file=file)
        assert await search_chunks(pg_db, query="photovoltaic panels", user_id=user_id)

        use_provider(monkeypatch, NewerHashing())

        assert await search_chunks(pg_db, query="photovoltaic panels", user_id=user_id) == []

    async def test_re_embedding_makes_them_searchable_again(
        self, pg_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)
        await index_file(pg_db, file=file)

        use_provider(monkeypatch, NewerHashing())
        await index_file(pg_db, file=file)

        assert await chunk_models(pg_db) == {"hashing:hashing-v2:512"}
        assert await search_chunks(pg_db, query="photovoltaic panels", user_id=user_id)

    async def test_a_failed_re_embed_keeps_the_old_chunks(
        self, pg_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Embedding happens before the delete, so an outage cannot leave a
        file with nothing searchable."""
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)
        written = await index_file(pg_db, file=file)

        use_provider(monkeypatch, BrokenProvider())
        with pytest.raises(ModelUnavailableError):
            await index_file(pg_db, file=file)
        await pg_db.rollback()

        stored = (await pg_db.execute(select(FileChunk))).scalars().all()
        assert len(stored) == written
        assert await chunk_models(pg_db) == {"hashing:hashing-v1:512"}

    async def test_a_semantic_provider_matches_meaning_not_words(
        self, pg_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = ConceptProvider()
        use_provider(monkeypatch, provider)
        user_id = await make_user(pg_db, "ada@example.com")
        await index_file(
            pg_db,
            file=await make_file(
                pg_db, user_id=user_id, text="The mechanic will fix the vehicle.",
                filename="garage.txt",
            ),
        )
        await index_file(
            pg_db,
            file=await make_file(
                pg_db, user_id=user_id, text="A sourdough loaf.", filename="bakery.txt"
            ),
        )

        found = await search_chunks(pg_db, query="car repair", user_id=user_id)

        # No word of the question appears in garage.txt.
        assert [chunk.filename for chunk in found] == ["garage.txt"]
        assert provider.purposes[-1] == "query"


class TestFilesNeedingIndex:
    async def test_finds_unindexed_and_stale_files_only(
        self, pg_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        never = await make_file(pg_db, user_id=user_id, text=SOLAR)
        stale = await make_file(pg_db, user_id=user_id, text=BAKING)
        await index_file(pg_db, file=stale)

        use_provider(monkeypatch, NewerHashing())
        fresh = await make_file(pg_db, user_id=user_id, text=SOLAR)
        await index_file(pg_db, file=fresh)
        empty = await make_file(pg_db, user_id=user_id, text="x")
        empty.status = FileStatus.EXTRACTED_EMPTY
        empty.extracted_text = None
        await pg_db.commit()

        found = await files_needing_index(pg_db, identity=NewerHashing().identity)

        assert set(found) == {never.id, stale.id}

    async def test_can_be_narrowed_to_one_user(self, pg_db: AsyncSession) -> None:
        ada = await make_user(pg_db, "ada@example.com")
        bob = await make_user(pg_db, "bob@example.com")
        ada_file = await make_file(pg_db, user_id=ada, text=SOLAR)
        await make_file(pg_db, user_id=bob, text=SOLAR)

        found = await files_needing_index(
            pg_db, identity="hashing:hashing-v1:512", user_id=ada
        )

        assert found == [ada_file.id]


class TestReindexCommand:
    async def test_dry_run_changes_nothing(
        self, pg_db: AsyncSession, pg_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        await make_file(pg_db, user_id=user_id, text=SOLAR)

        indexed, failed = await reindex.run(dry_run=True, session_factory=pg_session_factory)

        assert (indexed, failed) == (0, 0)
        assert (await pg_db.execute(select(FileChunk))).scalars().all() == []

    async def test_backfills_then_has_nothing_left_to_do(
        self, pg_db: AsyncSession, pg_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Idempotent: a second run finds no work."""
        user_id = await make_user(pg_db, "ada@example.com")
        await make_file(pg_db, user_id=user_id, text=SOLAR)
        await make_file(pg_db, user_id=user_id, text=BAKING)

        assert await reindex.run(session_factory=pg_session_factory) == (2, 0)
        assert await reindex.run(session_factory=pg_session_factory) == (0, 0)
        assert await search_chunks(pg_db, query="sourdough starter", user_id=user_id)

    async def test_migrates_stale_chunks_to_the_active_model(
        self,
        pg_db: AsyncSession,
        pg_session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)
        await index_file(pg_db, file=file)

        use_provider(monkeypatch, NewerHashing())
        assert await reindex.run(session_factory=pg_session_factory) == (1, 0)

        assert await chunk_models(pg_db) == {"hashing:hashing-v2:512"}

    async def test_limit_makes_it_resumable(
        self, pg_db: AsyncSession, pg_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        for text in (SOLAR, BAKING, SOLAR):
            await make_file(pg_db, user_id=user_id, text=text)

        assert await reindex.run(limit=2, session_factory=pg_session_factory) == (2, 0)
        assert await reindex.run(session_factory=pg_session_factory) == (1, 0)

    async def test_a_failure_is_reported_and_leaves_old_chunks(
        self,
        pg_db: AsyncSession,
        pg_session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        file = await make_file(pg_db, user_id=user_id, text=SOLAR)
        written = await index_file(pg_db, file=file)

        use_provider(monkeypatch, BrokenProvider())
        assert await reindex.run(session_factory=pg_session_factory) == (0, 1)

        stored = (await pg_db.execute(select(FileChunk))).scalars().all()
        assert len(stored) == written
        assert await chunk_models(pg_db) == {"hashing:hashing-v1:512"}

    async def test_never_prints_document_text(
        self,
        pg_db: AsyncSession,
        pg_session_factory: async_sessionmaker[AsyncSession],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        user_id = await make_user(pg_db, "ada@example.com")
        await make_file(pg_db, user_id=user_id, text=SOLAR, filename="secret-plan.txt")

        await reindex.run(session_factory=pg_session_factory)

        out = capsys.readouterr().out
        assert "Photovoltaic" not in out
        assert "secret-plan" not in out
