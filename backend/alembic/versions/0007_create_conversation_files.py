"""create conversation_files

Revision ID: 0007_conversation_files
Revises: 0006_file_content_sha256
Create Date: files in chats

Records which files were added to which conversation, so that questions in a
chat always draw on the files uploaded into it. See
app/models/conversation_file.py.

Both foreign keys cascade: deleting a conversation removes its links (never
the files), and deleting a file removes it from every chat.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007_conversation_files"
down_revision: str | None = "0006_file_content_sha256"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_files",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_files_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["files.id"],
            name=op.f("fk_conversation_files_file_id_files"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "conversation_id", "file_id", name=op.f("pk_conversation_files")
        ),
    )
    op.create_index(
        op.f("ix_conversation_files_file_id"),
        "conversation_files",
        ["file_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_conversation_files_file_id"), table_name="conversation_files")
    op.drop_table("conversation_files")
