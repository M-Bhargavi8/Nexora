"""Re-embed files whose chunks are missing or come from another model.

Use it when:

* the embedding provider or model changed (`EMBEDDING_PROVIDER`,
  `EMBEDDING_MODEL`) -- old chunks stay stored but are not searchable until
  re-embedded, because vectors from two models are not comparable;
* files were uploaded before Phase 6 and have no chunks at all;
* indexing failed at upload time (the provider was down, a timeout).

    cd backend
    .venv/Scripts/python.exe -m scripts.reindex --dry-run
    .venv/Scripts/python.exe -m scripts.reindex

**Safe to interrupt and run again.** Each file is embedded first and then
swapped in a single transaction, so a file always has either its old chunks or
its new ones, never none and never a mix. A finished file no longer matches the
query, so a second run picks up only what is left -- resumable and idempotent
without any progress file.

**It spends money with a hosted provider.** Every chunk of every selected file
is embedded again. Run `--dry-run` first to see how many files that is.

A file that fails is logged, left exactly as it was, and skipped; the exit code
is 1 if any failed, so a scheduler notices. It never prints document text.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from app.ai.gateway import get_embedding_provider
from app.db.session import get_engine, get_session_factory
from app.models.file import File
from app.services.retrieval import files_needing_index, index_file


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.reindex")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list how many files need indexing; embed and write nothing",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="stop after this many files (default: all)",
    )
    parser.add_argument(
        "--user-id",
        type=uuid.UUID,
        default=None,
        help="only this user's files",
    )
    return parser.parse_args(argv)


async def run(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    user_id: uuid.UUID | None = None,
    session_factory=None,  # noqa: ANN001 - injectable for tests
) -> tuple[int, int]:
    """Index what needs it. Returns (indexed, failed)."""
    factory = session_factory or get_session_factory()
    identity = get_embedding_provider().identity
    print(f"Active embedding model: {identity}")

    indexed = failed = seen = 0
    after: uuid.UUID | None = None

    while limit is None or seen < limit:
        page_size = 50 if limit is None else min(50, limit - seen)
        async with factory() as db:
            page = await files_needing_index(
                db, identity=identity, after=after, limit=page_size, user_id=user_id
            )
        if not page:
            break

        for file_id in page:
            after = file_id
            seen += 1
            if dry_run:
                continue
            # One session per file, so a failure rolls back only that file.
            async with factory() as db:
                file = await db.get(File, file_id)
                if file is None:
                    # Deleted since the page was read. Nothing to do.
                    continue
                try:
                    chunks = await index_file(db, file=file)
                except Exception as error:  # noqa: BLE001 - report and move on
                    await db.rollback()
                    failed += 1
                    print(f"  FAILED {file_id}: {type(error).__name__}")
                else:
                    indexed += 1
                    print(f"  indexed {file_id}: {chunks} chunks")

    if dry_run:
        print(f"{seen} file(s) need indexing. Nothing was changed (--dry-run).")
    else:
        print(f"Done: {indexed} indexed, {failed} failed.")
    return indexed, failed


async def _main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        _, failed = await run(
            dry_run=args.dry_run, limit=args.limit, user_id=args.user_id
        )
    finally:
        await get_engine().dispose()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
