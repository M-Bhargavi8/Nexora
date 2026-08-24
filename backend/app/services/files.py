"""File business logic: store, extract, list, delete.

Sits above three narrower modules -- `upload` decides what a file is,
`storage` owns the bytes, `extraction` reads them -- and is the only place
that knows about all three plus the database.

The ordering in `create_file` is the part worth understanding. Bytes are
written before the row exists, so a crash between the two leaves an orphaned
file rather than a row pointing at nothing. That is the right way round: a
stray file wastes disk, while a row promising content that is not there breaks
every reader of it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.file import File, FileStatus
from app.services import storage
from app.services.extraction import (
    ExtractionFailed,
    extract_text,
    validate_image_async,
)
from app.services.upload import (
    UploadRejected,
    detect_kind,
    sanitize_filename,
)

logger = get_logger(__name__)


async def create_file(
    db: AsyncSession, *, user_id: uuid.UUID, stream: BinaryIO, filename: str
) -> File:
    """Validate, store and parse one upload.

    Raises `UploadRejected` (the caller turns it into a 400) when the file is
    not something NEXORA accepts. A file that is accepted but cannot be parsed
    is **not** an error: it is stored with a status saying so, because a
    scanned PDF or a corrupt spreadsheet is still the user's file and deleting
    it out from under them would be worse than telling them the truth.
    """
    settings = get_settings()
    safe_name = sanitize_filename(filename)

    try:
        storage_key, size, head = await storage.save_upload(
            stream, max_bytes=settings.max_upload_bytes
        )
    except storage.UploadTooLarge as error:
        raise UploadRejected(str(error)) from error
    except ValueError as error:  # empty upload
        raise UploadRejected(str(error)) from error

    # Identification happens *after* writing, because it needs the leading
    # bytes and streaming them to disk is how we got them without holding the
    # whole file in memory. Anything rejected here is deleted immediately.
    try:
        kind = detect_kind(head, safe_name)
    except UploadRejected:
        storage.delete_stored_file(storage_key)
        raise

    path = storage.path_for(storage_key)
    status = FileStatus.READY
    text: str | None = None
    page_count: int | None = None
    error_message: str | None = None

    try:
        if not kind.extractable:
            # An image. Pillow decoding the header is what proves the magic
            # bytes were not simply glued to the front of something else.
            width, height = await validate_image_async(path)
            status = FileStatus.UNSUPPORTED
            error_message = (
                f"Images are stored but not yet read ({width}x{height}). "
                "Reading them needs a vision model."
            )
        else:
            extraction = await extract_text(path, kind)
            text = extraction.text
            page_count = extraction.page_count
            if extraction.truncated:
                error_message = (
                    "This file was longer than the extraction limit, so only "
                    "the first part was read."
                )
            if not text.strip():
                status = FileStatus.EXTRACTED_EMPTY
                text = None
                error_message = (
                    "No text could be read from this file. If it is a scan, it "
                    "contains images rather than text."
                )
    except ExtractionFailed as failure:
        # The file stays. The user uploaded it, it is a valid format, and we
        # simply could not read it -- which is information, not a reason to
        # discard their data.
        status = FileStatus.FAILED
        error_message = str(failure)
        logger.warning(
            "Extraction failed for an accepted file",
            extra={"storage_key": storage_key, "content_type": kind.content_type},
        )

    file = File(
        user_id=user_id,
        filename=safe_name,
        storage_key=storage_key,
        content_type=kind.content_type,
        size_bytes=size,
        status=status,
        extracted_text=text,
        page_count=page_count,
        error=error_message,
    )
    db.add(file)
    await db.commit()
    await db.refresh(file)

    logger.info(
        "File uploaded",
        extra={
            "file_id": str(file.id),
            "content_type": kind.content_type,
            "size_bytes": size,
            "status": status.value,
        },
    )
    return file


async def get_file(
    db: AsyncSession, *, file_id: uuid.UUID, user_id: uuid.UUID
) -> File | None:
    """One file **belonging to this user**, or None.

    The `user_id` filter is the authorization, exactly as for conversations. A
    file id is a UUID, which is unguessable in practice -- but "hard to guess"
    is not "not allowed", and someone else's document is the last thing that
    should be readable by anyone who obtains an identifier.
    """
    result = await db.execute(
        select(File).where(File.id == file_id, File.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_files(
    db: AsyncSession, *, user_id: uuid.UUID, limit: int = 30, offset: int = 0
) -> tuple[Sequence[File], bool]:
    """One page of this user's files, newest first, plus whether more follow.

    Same one-extra-row trick as the conversation list: asking for `limit + 1`
    answers "is there another page?" without a second `COUNT(*)`.
    """
    result = await db.execute(
        select(File)
        .where(File.user_id == user_id)
        # `id` breaks ties so that two files uploaded in the same clock tick
        # cannot swap places between page requests, which would make
        # pagination skip or repeat a row.
        .order_by(File.created_at.desc(), File.id.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    return rows[:limit], len(rows) > limit


async def delete_file(db: AsyncSession, *, file: File) -> None:
    """Delete a file: the row and the bytes.

    The row goes first. If the unlink then fails, the user sees the file gone
    -- which is what they asked for -- and an orphaned blob is left for
    cleanup. The reverse order risks a row that survives while its content does
    not, which reads as data corruption to everything downstream.
    """
    storage_key = file.storage_key
    await db.delete(file)
    await db.commit()
    storage.delete_stored_file(storage_key)
    logger.info("File deleted", extra={"storage_key": storage_key})
