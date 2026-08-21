"""create users and sessions

Revision ID: 0001_users_sessions
Revises:
Create Date: Phase 2 -- Authentication

The first migration. It creates the two tables authentication needs.

Read `downgrade()` as carefully as `upgrade()`: it is what runs when a
deployment has to be rolled back, and a migration that cannot be reversed
turns a bad release into an outage.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001_users_sessions"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        # sa.Uuid is SQLAlchemy's portable UUID type: a native uuid column on
        # PostgreSQL, a 32-character string on SQLite.
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    # Unique *and* indexed: uniqueness is the security guarantee that two
    # accounts cannot share an email, and the index makes the login lookup
    # fast. A unique index provides both.
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_sessions_user_id_users"),
            # Enforced by the database, so a session can never outlive the
            # user it authenticates as -- even if a row is deleted by a
            # script that bypasses the ORM.
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(
        op.f("ix_sessions_token_hash"), "sessions", ["token_hash"], unique=True
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"])


def downgrade() -> None:
    # Reverse order: `sessions` references `users`, so it must go first or
    # the foreign key blocks the drop.
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_token_hash"), table_name="sessions")
    op.drop_table("sessions")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
