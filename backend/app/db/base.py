"""The declarative base that every ORM model inherits from.

Every model subclasses ``Base`` and thereby registers itself in
``Base.metadata``, which is how Alembic discovers the schema.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic names for indexes and constraints.
#
# Left to itself, a database assigns its own names to unnamed constraints,
# and they differ between PostgreSQL and SQLite. A migration that says
# "drop the unique constraint on users.email" then cannot find it by name.
# Fixing the naming pattern here means every constraint has a predictable
# name everywhere. This must be set before any table is defined -- adding it
# later renames every existing constraint.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all NEXORA ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
