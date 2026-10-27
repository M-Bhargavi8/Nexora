"""Fill `files.content_sha256` for files uploaded before migration 0006.

Without a hash, an older file cannot be recognised when the same bytes are
uploaded again, so it would be stored a second time.

    cd backend
    .venv/Scripts/python.exe -m scripts.backfill_file_hashes --dry-run
    .venv/Scripts/python.exe -m scripts.backfill_file_hashes

Read-only on the bytes; writes only the hash column. Safe to interrupt and run
again: a hashed row no longer matches the query. A row whose bytes are missing
from disk is reported and skipped.

It **reports** how many groups of duplicates already exist, but
never deletes any. Which copy to keep -- and its filename and history -- is the
owner's decision, made on the Files page.

Prints ids and counts only; never filenames or content.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys

from sqlalchemy import func, select

from app.db.session import get_engine, get_session_factory
from app.models.file import File
from app.services import storage

_READ_SIZE = 1024 * 1024


def _hash_file(storage_key: str) -> str | None:
    path = storage.path_for(storage_key)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while block := source.read(_READ_SIZE):
                digest.update(block)
    except FileNotFoundError:
        return None
    return digest.hexdigest()


async def run(*, dry_run: bool = False, session_factory=None) -> tuple[int, int]:  # noqa: ANN001
    """Hash what is missing. Returns (hashed, missing_bytes)."""
    factory = session_factory or get_session_factory()
    hashed = missing = 0

    async with factory() as db:
        rows = (
            await db.execute(
                select(File.id, File.storage_key).where(File.content_sha256.is_(None))
            )
        ).all()
        print(f"{len(rows)} file(s) have no content hash.")

        for file_id, storage_key in rows:
            if dry_run:
                continue
            # Reading and hashing is blocking I/O; keep it off the event loop.
            digest = await asyncio.to_thread(_hash_file, storage_key)
            if digest is None:
                missing += 1
                print(f"  MISSING BYTES {file_id}")
                continue
            file = await db.get(File, file_id)
            if file is not None:
                file.content_sha256 = digest
                await db.commit()
                hashed += 1

        groups = (
            await db.execute(
                select(func.count())
                .select_from(
                    select(File.user_id, File.content_sha256)
                    .where(File.content_sha256.is_not(None))
                    .group_by(File.user_id, File.content_sha256)
                    .having(func.count() > 1)
                    .subquery()
                )
            )
        ).scalar_one()

    if dry_run:
        print("Nothing was changed (--dry-run).")
    else:
        print(f"Done: {hashed} hashed, {missing} with missing bytes.")
    print(f"Duplicate groups (same user, same bytes): {groups}. None were deleted.")
    return hashed, missing


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.backfill_file_hashes")
    parser.add_argument("--dry-run", action="store_true", help="count only; change nothing")
    args = parser.parse_args(argv)
    try:
        _, missing = await run(dry_run=args.dry_run)
    finally:
        await get_engine().dispose()
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
