"""Request and response schemas for files."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

# How much extracted text the list endpoint returns per file. The full text of
# a 500-page PDF has no business in a sidebar payload; the detail endpoint
# serves it when it is actually wanted.
PREVIEW_CHARACTERS = 400


class FileResponse(BaseModel):
    """A stored file's metadata, as returned to its owner.

    Deliberately omits `storage_key`. The client has no use for it, and it is
    the value that maps to a path on the server -- there is no reason to put
    it where a browser, a screenshot or a bug report can carry it away.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    status: str
    page_count: int | None = None
    # Present when something is worth telling the user: extraction failed, the
    # text was truncated, or the format is stored but not read.
    error: str | None = None
    created_at: datetime
    # Whether there is any text to fetch, so the UI can decide what to offer
    # without downloading it first.
    has_text: bool = False


class FileDetailResponse(FileResponse):
    """A file together with the text extracted from it."""

    extracted_text: str | None = None


class FilePage(BaseModel):
    """One page of the file list."""

    items: list[FileResponse]
    limit: int
    offset: int
    has_more: bool
