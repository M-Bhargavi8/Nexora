"""Putting uploaded bytes on disk, and taking them off again.

The database stores *metadata*; this module owns the bytes. Keeping them apart
matters because they fail differently -- a transaction rolls back, a file on
disk does not -- and `save_upload` is written so that a failure never leaves a
half-written file behind claiming to be complete.

**Why local disk.** One machine, one directory, no extra infrastructure. The
interface here is deliberately narrow -- write, open, delete, given a storage
key -- so that swapping in object storage later is a new implementation of
three functions rather than a change spread through the request handlers.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import BinaryIO

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# How much is read from the stream at a time. Large enough that writing a 20MB
# file is not thousands of syscalls, small enough that concurrent uploads do
# not each hold a large buffer.
CHUNK_SIZE = 64 * 1024


class UploadTooLarge(Exception):
    """The upload exceeded the configured size limit."""

    def __init__(self, limit_bytes: int) -> None:
        super().__init__(f"File is larger than {limit_bytes // (1024 * 1024)}MB.")
        self.limit_bytes = limit_bytes


def new_storage_key() -> str:
    """A fresh, opaque name for a stored file.

    A UUID with no dashes and **no extension**. Two consequences, both
    intentional: nothing about the user's filename can influence the path, and
    a web server that happens to be pointed at this directory has no extension
    to map to a handler -- so an uploaded file cannot be served as a script.
    """
    return uuid.uuid4().hex


def _upload_root() -> Path:
    root = get_settings().upload_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def path_for(storage_key: str) -> Path:
    """Where the bytes for `storage_key` live.

    SECURITY: the key must be one this application generated -- 32 hex
    characters. The check is not decoration. This function is the single place
    a path is built, so validating here means a traversal attempt cannot reach
    the filesystem no matter which caller made the mistake. Belt and braces:
    callers only ever pass keys read back from our own database column.
    """
    if len(storage_key) != 32 or not all(
        character in "0123456789abcdef" for character in storage_key
    ):
        raise ValueError(f"Refusing to build a path from an untrusted key: {storage_key!r}")

    return _upload_root() / storage_key


async def save_upload(stream: BinaryIO, *, max_bytes: int) -> tuple[str, int, bytes]:
    """Stream an upload to disk, stopping if it exceeds `max_bytes`.

    Returns the storage key, the number of bytes written, and the first chunk
    (which the caller sniffs to identify the format).

    **Why the limit is enforced here rather than from `Content-Length`.** That
    header is supplied by the client. It can understate the real size, be
    absent entirely on a chunked upload, or be a lie. Counting bytes as they
    arrive is the only number that cannot be forged -- and it stops *while*
    the file is arriving, so a 4GB upload costs 20MB of writes rather than
    4GB of them.

    On any failure the partial file is deleted before the exception
    propagates. A file on disk with no database row is invisible garbage; a
    truncated file with a row would be worse, because it would look valid.
    """
    storage_key = new_storage_key()
    destination = path_for(storage_key)
    written = 0
    head = b""

    try:
        with destination.open("wb") as sink:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break

                if not head:
                    head = chunk[:64]

                written += len(chunk)
                if written > max_bytes:
                    raise UploadTooLarge(max_bytes)

                sink.write(chunk)
    except Exception:
        # Includes UploadTooLarge. Whatever went wrong, the bytes already
        # written are not a usable file and must not be left behind.
        destination.unlink(missing_ok=True)
        raise

    if written == 0:
        destination.unlink(missing_ok=True)
        raise ValueError("The uploaded file is empty.")

    return storage_key, written, head


def delete_stored_file(storage_key: str) -> None:
    """Remove the bytes for `storage_key`, if they are still there.

    Never raises for a file that is already gone. Deletion is meant to be
    idempotent: a retry after a partial failure should finish the job, not
    fail because the first attempt succeeded.
    """
    try:
        path_for(storage_key).unlink(missing_ok=True)
    except OSError:
        # The database row is the record of what exists. Failing the whole
        # request because the bytes could not be unlinked would leave the user
        # unable to delete anything; log it and let the row go.
        logger.exception(
            "Could not delete stored bytes", extra={"storage_key": storage_key}
        )
