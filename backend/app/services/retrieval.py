"""Indexing files into searchable chunks, and searching them.

This module holds the two halves of RAG: writing vectors when a file is
uploaded, and reading the nearest ones when a question is asked.

**The security property that matters most in this phase.** Retrieval reaches
into stored documents and pastes what it finds into a prompt. If a search ever
returned a chunk belonging to someone else, that text would be handed to the
model and then, in the reply, to the wrong person -- a data breach delivered in
readable prose. Every query here filters on `user_id`, and there is no code
path that searches without one: `search_chunks` takes it as a required
keyword-only argument rather than an optional filter.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import EmbeddingProvider
from app.ai.gateway import get_embedding_provider
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.chunk import FileChunk
from app.models.file import File, FileStatus
from app.services.chunking import chunk_text

logger = get_logger(__name__)


#: How many rows to read per wanted result, leaving room to drop repeats.
_DUPLICATE_OVERFETCH = 3


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A passage found by a search, with where it came from and how well it fit."""

    chunk_id: uuid.UUID
    file_id: uuid.UUID
    filename: str
    position: int
    content: str
    #: Cosine similarity in [0, 1]. Higher is closer.
    similarity: float


async def index_file(db: AsyncSession, *, file: File) -> int:
    """Split a file's extracted text into chunks and embed them.

    Returns the number of chunks written.

    Safe to call again for the same file: existing chunks are deleted first, so
    re-indexing replaces rather than accumulates. Without that, a file indexed
    twice would have every passage stored twice, and a search would fill the
    model's context with the same text repeated.
    """
    if not file.extracted_text or file.status is not FileStatus.READY:
        # Nothing to index. An image, a scan with no text layer, or a file
        # whose parse failed -- all legitimate, none searchable.
        return 0

    settings = get_settings()
    pieces = chunk_text(
        file.extracted_text,
        size=settings.chunk_size_characters,
        overlap=settings.chunk_overlap_characters,
    )
    if not pieces:
        return 0

    provider = get_embedding_provider()
    # Embed *before* touching the database. A hosted provider is a network
    # call that can be slow or fail; doing it first means no write transaction
    # is held open across it, and a failure leaves the file's existing chunks
    # exactly as they were -- still searchable under their own model.
    #
    # One batched call rather than one per chunk. Hosted embedding APIs charge
    # and rate-limit per request, so a 200-chunk document must not be 200 round
    # trips.
    vectors = await provider.embed(
        [piece.content for piece in pieces], purpose="document"
    )
    _check_vectors(provider, vectors, expected=len(pieces))

    # Delete and insert in one transaction, so a re-index is atomic: readers
    # see the old chunks or the new ones, never a file with none.
    await db.execute(delete(FileChunk).where(FileChunk.file_id == file.id))

    for position, (piece, vector) in enumerate(zip(pieces, vectors, strict=True)):
        db.add(
            FileChunk(
                file_id=file.id,
                # Copied from the file rather than joined at query time -- see
                # the note on the column.
                user_id=file.user_id,
                position=position,
                content=piece.content,
                start_offset=piece.start_offset,
                end_offset=piece.end_offset,
                embedding=vector,
                embedding_model=provider.identity,
            )
        )

    await db.commit()
    logger.info(
        "File indexed for retrieval",
        extra={
            "file_id": str(file.id),
            "chunks": len(pieces),
            "embedding_model": provider.identity,
        },
    )
    return len(pieces)


async def file_is_indexed(db: AsyncSession, *, file: File) -> bool:
    """Whether `file` already has chunks from the active embedding model.

    A file with no text counts as indexed: there is nothing to do for it.
    """
    if not file.extracted_text or file.status is not FileStatus.READY:
        return True
    identity = get_embedding_provider().identity
    result = await db.execute(
        select(FileChunk.id)
        .where(
            FileChunk.file_id == file.id,
            FileChunk.embedding_model == identity,
        )
        .limit(1)
    )
    if result.first() is None:
        return False
    stale = await db.execute(
        select(FileChunk.id)
        .where(
            FileChunk.file_id == file.id,
            FileChunk.embedding_model != identity,
        )
        .limit(1)
    )
    return stale.first() is None


def _check_vectors(
    provider: EmbeddingProvider, vectors: list[list[float]], *, expected: int
) -> None:
    """Refuse vectors that cannot be stored or compared correctly.

    A provider that drops or reorders results would silently pair each chunk
    with another chunk's meaning, and retrieval would return confidently wrong
    passages. A vector of the wrong width either fails at insert or, worse, is
    compared against the wrong space. Both must fail here, loudly.
    """
    if len(vectors) != expected:
        raise ValueError(
            f"Embedding provider returned {len(vectors)} vectors "
            f"for {expected} texts."
        )
    for vector in vectors:
        if len(vector) != provider.dimensions:
            raise ValueError(
                f"Embedding provider {provider.identity} returned a "
                f"{len(vector)}-dimension vector."
            )


async def embed_query(query: str) -> list[float]:
    """Embed a question once, for callers that search more than once with it.

    Each embedding is a paid provider call that adds latency before the first
    token, so two searches for one question must share one vector.
    """
    provider = get_embedding_provider()
    vectors = await provider.embed([query], purpose="query")
    _check_vectors(provider, vectors, expected=1)
    return vectors[0]


async def search_chunks(
    db: AsyncSession,
    *,
    query: str,
    user_id: uuid.UUID,
    limit: int | None = None,
    min_similarity: float | None = None,
    file_ids: list[uuid.UUID] | None = None,
    query_vector: list[float] | None = None,
) -> list[RetrievedChunk]:
    """Find the passages most relevant to `query`, **for this user only**.

    `user_id` is keyword-only and required. It is the authorization, and making
    it impossible to call this function without one is the point: an optional
    filter is a filter somebody eventually forgets.

    Returns at most `limit` chunks, ordered most relevant first, and drops
    anything below `min_similarity`. That floor matters more than it looks --
    without it a search *always* returns its k nearest chunks however
    irrelevant, so asking about the weather with a tax return uploaded yields
    five passages about tax, presented as though they bore on the question.
    Returning nothing is the honest answer when the documents do not address it.

    `query_vector`, from `embed_query`, skips embedding `query` again. It must
    come from the active provider, which `embed_query` guarantees.
    """
    settings = get_settings()
    limit = limit if limit is not None else settings.retrieval_top_k
    threshold = (
        min_similarity if min_similarity is not None else settings.retrieval_min_similarity
    )

    if not query.strip():
        return []

    provider = get_embedding_provider()
    if query_vector is None:
        query_vector = await embed_query(query)

    # `cosine_distance` is pgvector's `<=>` operator: 0 means identical
    # direction, 1 means unrelated, 2 means opposite. Similarity is the
    # complement, which is the number people expect to reason about.
    distance = FileChunk.embedding.cosine_distance(query_vector)

    statement = (
        select(FileChunk, File.filename, distance.label("distance"))
        .join(File, File.id == FileChunk.file_id)
        # THE authorization filter. Never remove this to "simplify" the query.
        .where(FileChunk.user_id == user_id)
        # Only vectors from the model that embedded the query. A distance
        # between two models' vectors is a number, not a meaning; chunks from
        # an older model wait for `scripts.reindex` instead of being compared.
        .where(FileChunk.embedding_model == provider.identity)
        .order_by(distance)
        # Over-fetch, because identical passages are dropped below and the
        # caller still deserves up to `limit` distinct ones.
        .limit(limit * _DUPLICATE_OVERFETCH)
    )

    if file_ids:
        # Scoping to specific files is how "ask about this document" works,
        # rather than searching everything the user has ever uploaded.
        statement = statement.where(FileChunk.file_id.in_(file_ids))

    result = await db.execute(statement)

    found: list[RetrievedChunk] = []
    seen: set[str] = set()
    for chunk, filename, chunk_distance in result.all():
        similarity = 1.0 - float(chunk_distance)
        if similarity < threshold or len(found) == limit:
            # Ordered by distance, so everything after this is worse too.
            break
        # The same passage stored twice -- two copies of a document, or a
        # PDF and the DOCX it was exported from -- would otherwise fill the
        # results with repeats and crowd out everything else. Keep the
        # closest copy; whitespace differences do not make a new passage.
        key = " ".join(chunk.content.split())
        if key in seen:
            continue
        seen.add(key)
        found.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                file_id=chunk.file_id,
                filename=filename,
                position=chunk.position,
                content=chunk.content,
                similarity=similarity,
            )
        )

    logger.info(
        "Retrieval search completed",
        extra={"user_id": str(user_id), "results": len(found)},
    )
    return found


async def count_chunks(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    """Whether this user has any searchable passages (0 or 1). Used to decide
    whether retrieval is worth attempting at all.

    Counts only chunks from the active embedding model -- chunks from another
    model are stored but not searchable until re-indexed.
    """
    identity = get_embedding_provider().identity
    result = await db.execute(
        select(FileChunk.id)
        .where(FileChunk.user_id == user_id, FileChunk.embedding_model == identity)
        .limit(1)
    )
    return len(result.all())


async def files_needing_index(
    db: AsyncSession,
    *,
    identity: str,
    after: uuid.UUID | None = None,
    limit: int = 50,
    user_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Ids of files with text that are not fully indexed under `identity`.

    That is: a READY file with extracted text that has either no chunks at all
    (uploaded before Phase 6, or its indexing failed) or any chunk written by
    a different embedding model.

    Keyset-paginated on `id` rather than offset, so a caller that skips a file
    which keeps failing still makes progress instead of re-reading it forever.
    This is an operator query across users; `user_id` narrows it to one.
    """
    any_chunk = exists().where(FileChunk.file_id == File.id)
    stale_chunk = exists().where(
        FileChunk.file_id == File.id, FileChunk.embedding_model != identity
    )
    # Ids only: a page of 50 full files could be 50MB of extracted text.
    statement = (
        select(File.id)
        .where(
            File.status == FileStatus.READY,
            File.extracted_text.is_not(None),
            or_(~any_chunk, stale_chunk),
        )
        .order_by(File.id)
        .limit(limit)
    )
    if after is not None:
        statement = statement.where(File.id > after)
    if user_id is not None:
        statement = statement.where(File.user_id == user_id)
    result = await db.execute(statement)
    return list(result.scalars().all())
