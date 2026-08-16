"""Alembic migration environment.

Alembic compares the tables declared in our ORM models against the tables
that actually exist in the database, and generates the SQL to close the gap.
That generated SQL is committed as a versioned migration file, so every
environment applies exactly the same schema changes in the same order.

Two things are customised from the generated template:

1. The database URL is read from application settings rather than from
   `alembic.ini`, which is committed to git and must never hold a password.
2. `target_metadata` points at our declarative `Base`, so autogenerate can
   see our models.
"""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make the `app` package importable regardless of where alembic is invoked
# from. alembic/env.py -> alembic -> backend
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402

# NOTE (Phase 2 onward): every model module must be imported here, or its
# table will be missing from Base.metadata and autogenerate will silently
# produce a migration that drops it. There are no models yet.
# e.g. from app.models import user  # noqa: F401

config = context.config

# Inject the real connection URL at runtime.
config.set_main_option("sqlalchemy.url", str(get_settings().database_url))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL to a file instead of running it.

    Useful when a DBA must review and apply changes by hand in production.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detect column type changes (e.g. VARCHAR(50) -> VARCHAR(100)),
        # which alembic ignores by default.
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Connect with the async engine and run migrations."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # a one-shot script needs no connection pool
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
