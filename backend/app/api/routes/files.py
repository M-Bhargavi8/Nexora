"""File endpoints: upload, list, read, download, delete.

Uploads arrive as `multipart/form-data` rather than JSON. That is not a style
choice -- JSON has no binary type, so sending a PDF through it means base64,
which inflates the payload by a third and forces the whole file into memory to
decode. Multipart streams, which is what lets `save_upload` enforce a size
limit while the bytes are still arriving.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File as FileParam, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse as FileDownloadResponse, Response

from app.api.dependencies import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rate_limit import SlidingWindowRateLimiter
from app.models.file import File
from app.schemas.file import (
    PREVIEW_CHARACTERS,
    FileDetailResponse,
    FilePage,
    FileResponse,
)
from app.services import storage
from app.services.files import create_file, delete_file, get_file, list_files
from app.services.upload import ACCEPTED_EXTENSIONS, UploadRejected

logger = get_logger(__name__)

router = APIRouter(prefix="/files", tags=["Files"])

_settings = get_settings()

# Keyed by user, like the chat limiter. Uploading costs disk and CPU rather
# than model tokens, but it is still work an authenticated caller can ask for
# repeatedly, and parsing is the expensive part.
upload_rate_limiter = SlidingWindowRateLimiter(
    max_attempts=_settings.upload_rate_limit_attempts,
    window_seconds=_settings.upload_rate_limit_window_seconds,
)


def reset_upload_rate_limits() -> None:
    """Clear the upload limiter. Used by tests to isolate one case from the next."""
    upload_rate_limiter.clear()


DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100

_FILE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
)


def _to_response(file: File) -> FileResponse:
    return FileResponse(
        id=file.id,
        filename=file.filename,
        content_type=file.content_type,
        size_bytes=file.size_bytes,
        status=file.status.value,
        page_count=file.page_count,
        error=file.error,
        created_at=file.created_at,
        has_text=bool(file.extracted_text),
    )


@router.post(
    "",
    response_model=FileDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file",
    description=(
        "Accepts `multipart/form-data`. The file's type is determined from its "
        "contents, not from the supplied filename or content type. Accepted: "
        + ", ".join(sorted(ACCEPTED_EXTENSIONS))
    ),
    responses={
        400: {"description": "The file was rejected by validation"},
        429: {"description": "Too many uploads"},
    },
)
async def upload_file(
    db: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, FileParam(description="The file to upload.")],
) -> FileDetailResponse:
    """Store an uploaded file and extract whatever text it contains."""
    if not upload_rate_limiter.allow(f"upload:{user.id}"):
        logger.warning("Upload rate limit exceeded", extra={"user_id": str(user.id)})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many uploads. Please wait a moment and try again.",
            headers={"Retry-After": str(upload_rate_limiter.window_seconds)},
        )

    try:
        stored = await create_file(
            db,
            user_id=user.id,
            # Starlette buffers the upload in a SpooledTemporaryFile: small
            # files stay in memory, larger ones spill to disk. `.file` is the
            # underlying synchronous handle, which `save_upload` reads in
            # bounded chunks.
            stream=file.file,
            # `or ""` because the filename is optional in the multipart spec.
            # `sanitize_filename` turns an empty name into "unnamed".
            filename=file.filename or "",
        )
    except UploadRejected as rejected:
        # 400, not 422: the request is well-formed multipart, and it is the
        # *content* that is unacceptable. A 422 here would be indistinguishable
        # from a schema mistake by the client.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(rejected)
        ) from rejected

    return FileDetailResponse(
        **_to_response(stored).model_dump(),
        extracted_text=stored.extracted_text,
    )


@router.get(
    "",
    response_model=FilePage,
    summary="List this user's files",
)
async def get_files(
    db: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FilePage:
    files, has_more = await list_files(db, user_id=user.id, limit=limit, offset=offset)
    return FilePage(
        items=[_to_response(file) for file in files],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


@router.get(
    "/{file_id}",
    response_model=FileDetailResponse,
    summary="Read one file's metadata and extracted text",
    responses={404: {"description": "No such file for this user"}},
)
async def get_file_detail(
    file_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    preview: Annotated[
        bool, Query(description="Return only the first part of the text.")
    ] = False,
) -> FileDetailResponse:
    file = await get_file(db, file_id=file_id, user_id=user.id)
    if file is None:
        raise _FILE_NOT_FOUND

    text = file.extracted_text
    if preview and text:
        text = text[:PREVIEW_CHARACTERS]

    return FileDetailResponse(**_to_response(file).model_dump(), extracted_text=text)


@router.get(
    "/{file_id}/download",
    summary="Download the original file",
    responses={404: {"description": "No such file for this user"}},
)
async def download_file(
    file_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> FileDownloadResponse:
    """Serve the stored bytes back to their owner.

    **The headers here are the security control, not decoration.** Serving
    user-uploaded content from the API's own origin is how a stored
    cross-site-scripting hole is created: an uploaded SVG or HTML file rendered
    *inline* executes with the privileges of this origin, and the session
    cookie is right there.

    So every download is sent as `application/octet-stream` with
    `Content-Disposition: attachment`, which tells the browser to save the file
    rather than render it, and `X-Content-Type-Options: nosniff`, which stops
    the browser from second-guessing the type and rendering it anyway. The
    real type is recorded in the database and shown in the UI; it is
    deliberately not what this response advertises.
    """
    file = await get_file(db, file_id=file_id, user_id=user.id)
    if file is None:
        raise _FILE_NOT_FOUND

    path = storage.path_for(file.storage_key)
    if not path.exists():
        # The row outlived its bytes. Reported as missing rather than as a 500,
        # because from the user's point of view the file is gone.
        logger.error(
            "Stored bytes are missing for a file row",
            extra={"file_id": str(file.id)},
        )
        raise _FILE_NOT_FOUND

    return FileDownloadResponse(
        path=path,
        media_type="application/octet-stream",
        # Starlette quotes and escapes this, so a filename cannot inject
        # additional header directives.
        filename=file.filename,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a file and its stored contents",
    responses={404: {"description": "No such file for this user"}},
)
async def remove_file(
    file_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    file = await get_file(db, file_id=file_id, user_id=user.id)
    # The same 404 whether the file does not exist or belongs to someone else,
    # so the response cannot be used to discover which ids are real.
    if file is None:
        raise _FILE_NOT_FOUND

    await delete_file(db, file=file)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
