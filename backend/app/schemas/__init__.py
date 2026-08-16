"""Pydantic schemas: the shape of data crossing the API boundary.

Schemas are deliberately separate from ORM models. A model describes what we
store; a schema describes what we accept and return. Keeping them apart stops
internal columns (password hashes, soft-delete flags) from leaking into API
responses just because someone added a column.
"""
