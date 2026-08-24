"""create file_chunks with pgvector

Revision ID: 0004_file_chunks
Revises: 0003_files
Create Date: Phase 6 -- RAG

The table that makes documents searchable by meaning rather than by keyword.

Three things here are worth reading rather than skimming:

* **`CREATE EXTENSION vector`.** The `pgvector/pgvector` image ships the
  extension files, but an extension still has to be enabled per database. Doing
  it in the migration means a fresh database is correct after `upgrade head`
  alone, with no manual step that someone will forget on the day it matters.

* **`user_id` is duplicated from `files`.** Deliberate denormalisation, and the
  only one in the schema. Every retrieval query filters by user, and reaching
  the owner through a join would put the ownership filter and the vector index
  on different tables -- forcing PostgreSQL to either scan vectors it will then
  discard, or join first and lose the index. It also makes the security filter
  easy to omit and hard to spot when reviewing a query.

* **The vector index is IVFFlat, and it is approximate.** Exact nearest-
  neighbour search reads every row; an index makes it fast by sometimes missing
  a true neighbour. That trade is normal for retrieval and worth stating out
  loud. `lists = 100` suits tens of thousands of rows; it wants raising as the
  table grows. Note that IVFFlat must be built on a populated table to choose
  good centroids -- on an empty one it produces a poor index, which is why a
  seeded database should reindex after a bulk import.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0004_file_chunks"
down_revision: str | None = "0003_files"
branch_labels: str | None = None
depends_on: str | None = None

# Must match app/models/chunk.py. pgvector requires a fixed dimension, and
# vectors from two different embedding models are not comparable anyway --
# so changing the model means a migration *and* re-embedding every chunk.
EMBEDDING_DIMENSIONS = 512


def upgrade() -> None:
    # Enabling an extension needs elevated privileges. If this fails, the
    # database user lacks them -- which is a deployment fact worth discovering
    # here rather than on the first search.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "file_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        # Denormalised owner -- see the module docstring.
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # Character offsets into the file's extracted text, so a citation can
        # point at where in the document a passage came from.
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["files.id"],
            name=op.f("fk_file_chunks_file_id_files"),
            # Deleting a file removes its chunks and their vectors in the same
            # transaction. This is the payoff for keeping vectors in PostgreSQL
            # rather than a separate store: no second system can be left
            # holding searchable copies of a deleted document.
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_file_chunks_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_chunks")),
        # Re-indexing a file must replace its chunks, not accumulate duplicates
        # that would each match a query and fill the context with the same
        # passage twice.
        sa.UniqueConstraint(
            "file_id", "position", name=op.f("uq_file_chunks_file_position")
        ),
    )

    # The index every retrieval query needs: "this user's chunks".
    op.create_index(
        op.f("ix_file_chunks_user_id"), "file_chunks", ["user_id"], unique=False
    )

    # The vector index.
    #
    # Created through `op.create_index` rather than raw SQL so that it matches
    # the Index declared on the model. An index that exists only in the
    # database is one Alembic autogenerate cannot see -- it would propose
    # dropping it, `alembic check` would fail, and the next generated migration
    # would quietly remove the vector index. The symptom is a query that still
    # returns correct results, only slowly.
    op.create_index(
        "ix_file_chunks_embedding",
        "file_chunks",
        ["embedding"],
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_file_chunks_embedding", table_name="file_chunks")
    op.drop_index(op.f("ix_file_chunks_user_id"), table_name="file_chunks")
    op.drop_table("file_chunks")
    # The `vector` extension is deliberately NOT dropped. Another table might
    # use it, and dropping an extension cascades to every column of its type --
    # a downgrade that silently destroys unrelated data is far worse than one
    # that leaves an unused extension enabled.
