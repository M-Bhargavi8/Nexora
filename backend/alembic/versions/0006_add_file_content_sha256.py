"""record a content hash for each uploaded file

Revision ID: 0006_file_content_sha256
Revises: 0005_chunk_embedding_model
Create Date: duplicate uploads

Adds `files.content_sha256` so that uploading a file the user already has can
reuse the stored copy instead of keeping, extracting and embedding it again.

Nullable, with no backfill here: a hash needs the file's bytes, which live on
disk rather than in the database, and a migration should change schema, not
read the upload directory. `python -m scripts.backfill_file_hashes` fills
existing rows.

The index is deliberately **not** unique. Rows uploaded before this revision
may already be duplicates, and a unique index would make the backfill fail on
exactly the data it exists to describe.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_file_content_sha256"
down_revision: str | None = "0005_chunk_embedding_model"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "files", sa.Column("content_sha256", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "ix_files_user_id_content_sha256",
        "files",
        ["user_id", "content_sha256"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_files_user_id_content_sha256", table_name="files")
    op.drop_column("files", "content_sha256")
