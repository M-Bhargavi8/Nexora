"""A retrievable piece of a file, and its embedding.

**Why chunks exist at all.** A model has a finite context window, and sending a
300-page document with every question would be slow, expensive, and worse: the
relevant paragraph gets diluted among hundreds of irrelevant ones. So a
document is split into passages, each passage is embedded once, and a question
retrieves only the few passages that actually bear on it.

**Why the vector lives beside the text.** It could live in a dedicated vector
database. It does not, because then two stores would have to be kept in step --
delete a file and you must remember to delete its vectors, in a different
system, without a transaction spanning both. pgvector puts the vector in a
PostgreSQL column, so a chunk and its embedding are inserted and deleted
together, by the same foreign key cascade as everything else.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import struct

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Dialect,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.ai.providers.hashing_embeddings import DIMENSIONS
from app.core.clock import utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.file import File
    from app.models.user import User

#: Length of every stored vector.
#:
#: Baked into the column, which is the constraint that makes this a real
#: decision rather than a setting: pgvector requires a fixed dimension, and
#: vectors from two different models are not comparable anyway. Changing the
#: embedding model therefore means a migration *and* re-embedding every chunk.
EMBEDDING_DIMENSIONS = DIMENSIONS


class PackedVector(TypeDecorator[list]):
    """Stores a list of floats as packed bytes. **SQLite only.**

    PostgreSQL is the real target and uses pgvector's `vector` type. This
    exists so the test suite, which runs on in-memory SQLite, can still
    exercise the parts of retrieval that are not PostgreSQL-specific: chunking
    a file, writing chunks, cascading deletes, ownership.

    Without it the alternative would be to skip indexing entirely on SQLite --
    which would mean the upload-to-chunk pipeline was never tested at all
    except against a real database, and would let a bug there hide behind a
    dialect check.

    What it deliberately does **not** provide is similarity search. There is no
    `<=>` operator here and no attempt to emulate one; vector queries are
    tested against real PostgreSQL. Emulating them would risk the suite passing
    against a Python reimplementation while production used a different one.
    """

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(
        self, value: list[float] | None, dialect: Dialect
    ) -> bytes | None:
        if value is None:
            return None
        # Little-endian 32-bit floats, matching pgvector's own storage width.
        return struct.pack(f"<{len(value)}f", *value)

    def process_result_value(
        self, value: bytes | None, dialect: Dialect
    ) -> list[float] | None:
        if value is None:
            return None
        return list(struct.unpack(f"<{len(value) // 4}f", value))


class FileChunk(Base):
    """One passage of a file, with the vector used to find it."""

    __tablename__ = "file_chunks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Denormalised from `files.user_id`, and the one place in NEXORA where a
    # duplicated owner column is the right call.
    #
    # Every retrieval query filters by user. Reaching the owner through a join
    # would mean the vector index and the ownership filter live on different
    # tables, so PostgreSQL must either scan vectors it will then discard, or
    # join first and lose the index. Worse, it makes the security filter easy
    # to leave out of a query and hard to see when reviewing one.
    #
    # The risk of denormalising -- two sources of truth disagreeing -- is
    # contained here because chunks are never re-owned: they are written once
    # with their file's owner and deleted with it.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Position within the file, 0-based. Lets a retrieved passage be shown in
    # context, and makes ordering stable.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Character offsets into the file's extracted text, so a citation can point
    # at *where* in the document a passage came from.
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)

    # The embedding.
    #
    # `with_variant` compiles this as pgvector's `vector(n)` on PostgreSQL and
    # as packed bytes on SQLite. The test suite builds its schema on SQLite,
    # which has no vector type, so without a variant *every* test in the suite
    # would fail at create_all -- including ones with nothing to do with
    # retrieval. Similarity search itself is PostgreSQL-only and is tested
    # against a real PostgreSQL instance; see tests/conftest.py.
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS).with_variant(PackedVector, "sqlite"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    file: Mapped[File] = relationship(back_populates="chunks")
    user: Mapped[User] = relationship()

    __table_args__ = (
        # A file cannot have two chunks at the same position -- re-indexing a
        # file must replace its chunks, not accumulate duplicates that would
        # each match a query and fill the context with the same passage twice.
        UniqueConstraint("file_id", "position", name="uq_file_chunks_file_position"),
        # The index every retrieval query needs: "this user's chunks".
        Index("ix_file_chunks_user_id", "user_id"),
        # The vector index.
        #
        # Declared here rather than as raw SQL in the migration, and that is
        # not a style preference. An index created with `op.execute` exists in
        # the database but not in `Base.metadata`, so Alembic autogenerate sees
        # an index the models do not declare and proposes **dropping** it --
        # `alembic check` fails, and the next autogenerated migration quietly
        # removes the vector index. The symptom would be a retrieval query that
        # still returns correct results, only slowly, which is exactly the kind
        # of regression nobody notices. (The same trap is documented on
        # messages.role in migration 0002.)
        #
        # `vector_cosine_ops` must match the operator the queries use (`<=>`).
        # An index built for a different distance function is silently ignored.
        #
        # IVFFlat is *approximate*: it is fast because it may miss a true
        # nearest neighbour. That trade is normal for retrieval and worth
        # stating out loud. `lists = 100` suits tens of thousands of rows and
        # wants raising as the table grows; the index also needs rebuilding
        # after a bulk import, because it picks its centroids from whatever is
        # present when it is built.
        Index(
            "ix_file_chunks_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:
        # SECURITY: never includes `content`. reprs reach logs and tracebacks,
        # and this is the user's document text.
        return f"<FileChunk id={self.id} file={self.file_id} pos={self.position}>"
