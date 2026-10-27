"""record which embedding model produced each chunk

Revision ID: 0005_chunk_embedding_model
Revises: 0004_file_chunks
Create Date: Gap A -- semantic embeddings

Adds `file_chunks.embedding_model`, so vectors from different models can never
be compared as though they were the same kind of number.

**The backfill value is a fact, not a guess.** Until this revision the only
embedding provider that existed was the offline hashing one at 512 dimensions,
so every chunk already stored was written by `hashing:hashing-v1:512`. The
server default fills existing rows with exactly that and is then dropped, so
new rows must always state their model explicitly -- a default would let a
future code path write a vector without saying where it came from.

Downgrade drops the column. The vectors themselves are untouched either way.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_chunk_embedding_model"
down_revision: str | None = "0004_file_chunks"
branch_labels: str | None = None
depends_on: str | None = None

_EXISTING_MODEL = "hashing:hashing-v1:512"


def upgrade() -> None:
    op.add_column(
        "file_chunks",
        sa.Column(
            "embedding_model",
            sa.String(length=200),
            nullable=False,
            server_default=_EXISTING_MODEL,
        ),
    )
    op.alter_column("file_chunks", "embedding_model", server_default=None)
    op.create_index(
        "ix_file_chunks_user_id_embedding_model",
        "file_chunks",
        ["user_id", "embedding_model"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_file_chunks_user_id_embedding_model", table_name="file_chunks")
    op.drop_column("file_chunks", "embedding_model")
