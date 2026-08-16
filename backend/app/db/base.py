"""The declarative base that every ORM model will inherit from.

No models exist yet -- Phase 2 adds the first one (``users``). This file
exists now so that the database wiring is complete and Alembic has a single,
known place to look for table metadata.

Every model subclasses ``Base``; each one registers itself in
``Base.metadata``, which is how Alembic discovers the schema and generates
migrations automatically.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all NEXORA ORM models."""
