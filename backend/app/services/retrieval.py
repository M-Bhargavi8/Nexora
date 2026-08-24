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

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import get_embedding_provider
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.chunk import FileChunk
from app.models.file import File, FileStatus
from app.services.chunking import chunk_text

logger = get_logger(__name__)


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

    # Delete first, in the same transaction as the insert below, so a re-index
    # is atomic: the file is never left with no chunks at all if embedding
    # fails part way.
    await db.execute(delete(FileChunk).where(FileChunk.file_id == file.id))

    provider = get_embedding_provider()
    # One batched call rather than one per chunk. Hosted embedding APIs charge
    # and rate-limit per request, so a 200-chunk document must not be 200 round
    # trips.
    vectors = await provider.embed([piece.content for piece in pieces])

    if len(vectors) != len(pieces):
        # A provider that drops or reorders results would silently pair each
        # chunk with another chunk's meaning, and retrieval would return
        # confidently wrong passages. Fail instead.
        raise ValueError(
            f"Embedding provider returned {len(vectors)} vectors "
            f"for {len(pieces)} chunks."
        )

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
            )
        )

    await db.commit()
    logger.info(
        "File indexed for retrieval",
        extra={"file_id": str(file.id), "chunks": len(pieces)},
    )
    return len(pieces)


async def search_chunks(
    db: AsyncSession,
    *,
    query: str,
    user_id: uuid.UUID,
    limit: int | None = None,
    min_similarity: float | None = None,
    file_ids: list[uuid.UUID] | None = None,
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
    """
    settings = get_settings()
    limit = limit if limit is not None else settings.retrieval_top_k
    threshold = (
        min_similarity if min_similarity is not None else settings.retrieval_min_similarity
    )

    if not query.strip():
        return []

    provider = get_embedding_provider()
    [query_vector] = await provider.embed([query])

    # `cosine_distance` is pgvector's `<=>` operator: 0 means identical
    # direction, 1 means unrelated, 2 means opposite. Similarity is the
    # complement, which is the number people expect to reason about.
    distance = FileChunk.embedding.cosine_distance(query_vector)

    statement = (
        select(FileChunk, File.filename, distance.label("distance"))
        .join(File, File.id == FileChunk.file_id)
        # THE authorization filter. Never remove this to "simplify" the query.
        .where(FileChunk.user_id == user_id)
        .order_by(distance)
        .limit(limit)
    )

    if file_ids:
        # Scoping to specific files is how "ask about this document" works,
        # rather than searching everything the user has ever uploaded.
        statement = statement.where(FileChunk.file_id.in_(file_ids))

    result = await db.execute(statement)

    found: list[RetrievedChunk] = []
    for chunk, filename, chunk_distance in result.all():
        similarity = 1.0 - float(chunk_distance)
        if similarity < threshold:
            # Ordered by distance, so everything after this is worse too.
            break
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
    """How many searchable passages this user has. Used to decide whether
    retrieval is worth attempting at all."""
    result = await db.execute(
        select(FileChunk.id).where(FileChunk.user_id == user_id).limit(1)
    )
    return len(result.all())
