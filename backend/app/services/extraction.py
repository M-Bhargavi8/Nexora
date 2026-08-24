"""Getting text out of an uploaded file.

Every function here parses a format NEXORA did not produce, which makes this
the most hostile code in the application: the input is chosen by whoever
uploaded it. The rules that follow from that:

* **Bound everything.** Each extractor stops at `max_extracted_characters`.
  DOCX and XLSX are ZIP archives, so a 200KB upload can legitimately hold
  hundreds of megabytes of text -- and a "zip bomb" is built to do exactly
  that. PDFs are bounded by page count as well, because parsing is the
  expensive part and a hostile file can have a great many pages.
* **Never trust a parser not to raise.** A malformed file is the normal case
  here, not the exception, so a failure to extract is recorded on the row as a
  status rather than turned into a 500.
* **Never leak the exception text.** Parser errors quote file paths and
  library internals. The user gets a sentence; the log gets the traceback.

These run in a worker thread (see `extract_text`), because every library below
is synchronous and CPU-bound. Calling them directly on the event loop would
freeze every other request -- including the streaming chat responses -- for as
long as a large PDF takes to parse.
"""

from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.upload import CSV, DOCX, PDF, TXT, XLSX, FileKind

logger = get_logger(__name__)


@dataclass(frozen=True)
class Extraction:
    """What came out of a file."""

    text: str
    # Pages for a PDF, sheets for a spreadsheet, None where the idea does not
    # apply.
    page_count: int | None = None
    # Set when the document was longer than the limit, so the caller can say so
    # rather than silently handing back a partial document.
    truncated: bool = False


class ExtractionFailed(Exception):
    """Parsing failed in a way the user should be told about, in plain words."""


def _join_bounded(pieces: list[str], limit: int) -> tuple[str, bool]:
    """Join text fragments, stopping once `limit` characters are reached."""
    out: list[str] = []
    total = 0
    for piece in pieces:
        if total >= limit:
            return "\n".join(out), True
        remaining = limit - total
        if len(piece) > remaining:
            out.append(piece[:remaining])
            return "\n".join(out), True
        out.append(piece)
        total += len(piece) + 1
    return "\n".join(out), False


def _extract_txt(path: Path, limit: int) -> Extraction:
    """Read a plain-text or Markdown file.

    `errors="replace"` rather than raising: a file that is *mostly* valid UTF-8
    with a stray byte is far more useful decoded with one replacement character
    than rejected outright. The content was already checked as text-like during
    upload, so this is about resilience, not about accepting binary.
    """
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        # One character more than the limit, so that "did it fit?" is answerable
        # without reading a 20MB file to find out.
        text = handle.read(limit + 1)

    truncated = len(text) > limit
    return Extraction(text=text[:limit], truncated=truncated)


def _extract_csv(path: Path, limit: int) -> Extraction:
    """Flatten a CSV into readable lines.

    Rows are rendered as `a | b | c` rather than raw comma-separated text. A
    model reads the delimiter as structure either way, but this survives values
    that themselves contain commas, which raw text does not.
    """
    lines: list[str] = []
    total = 0

    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        try:
            for row in csv.reader(handle):
                line = " | ".join(cell.strip() for cell in row)
                if not line.strip():
                    continue
                lines.append(line)
                total += len(line) + 1
                if total >= limit:
                    break
        except csv.Error as error:
            # A NUL or a gigantic unterminated quoted field. Recoverable: keep
            # whatever parsed cleanly rather than losing the whole file.
            logger.warning("Stopped parsing CSV early", extra={"reason": str(error)})

    text, truncated = _join_bounded(lines, limit)
    return Extraction(text=text, truncated=truncated)


def _extract_pdf(path: Path, limit: int) -> Extraction:
    """Pull the text layer out of a PDF.

    A PDF has no obligation to contain text at all -- a scan is a container of
    images, and yields nothing here. That is not a failure, and the caller
    reports it as `EXTRACTED_EMPTY`; making it readable needs OCR, which is not
    part of this phase.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    max_pages = get_settings().max_pdf_pages

    try:
        # An explicit handle rather than a path. Every parser in this module
        # opens the file this way so that the handle is closed even when
        # parsing raises -- on Windows an open handle makes the file
        # undeletable, so a leak here would mean a corrupt upload could never
        # be removed.
        with path.open("rb") as handle:
            reader = PdfReader(handle)
            total_pages = len(reader.pages)

            pieces: list[str] = []
            for page in reader.pages[:max_pages]:
                try:
                    pieces.append(page.extract_text() or "")
                except Exception:  # noqa: BLE001 - one bad page must not lose the rest
                    logger.warning("Skipped an unreadable PDF page")
                    continue
                if sum(len(piece) for piece in pieces) >= limit:
                    break
    except PdfReadError as error:
        raise ExtractionFailed("This PDF could not be read. It may be corrupt.") from error
    except Exception as error:  # noqa: BLE001 - pypdf raises a wide variety
        logger.exception("PDF extraction failed")
        raise ExtractionFailed("This PDF could not be read.") from error

    text, truncated = _join_bounded(pieces, limit)
    return Extraction(
        text=text,
        page_count=total_pages,
        truncated=truncated or total_pages > max_pages,
    )


def _extract_docx(path: Path, limit: int) -> Extraction:
    """Read paragraphs and table cells out of a Word document.

    Tables are included because in real documents they carry the facts --
    figures, dates, parameters. Reading only paragraphs looks fine on a memo
    and silently drops the substance of a report.
    """
    import docx

    try:
        # The `with` matters more than it looks. Given a *path*, python-docx
        # opens the archive itself and does not close it when it raises on a
        # malformed file -- and on Windows a file with an open handle cannot
        # be deleted, so a corrupt upload would become permanently
        # undeletable. Owning the handle here makes the cleanup ours.
        with path.open("rb") as handle:
            document = docx.Document(handle)
    except Exception as error:  # noqa: BLE001 - python-docx raises broadly
        logger.exception("DOCX extraction failed")
        raise ExtractionFailed(
            "This Word document could not be read. It may be corrupt."
        ) from error

    pieces = [
        paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()
    ]
    for table in document.tables:
        for row in table.rows:
            line = " | ".join(cell.text.strip() for cell in row.cells)
            if line.strip(" |"):
                pieces.append(line)

    text, truncated = _join_bounded(pieces, limit)
    return Extraction(text=text, truncated=truncated)


def _extract_xlsx(path: Path, limit: int) -> Extraction:
    """Read cell values out of a spreadsheet.

    `read_only=True` streams rows instead of building the whole workbook in
    memory -- the difference between bounded memory and none on a large file.
    `data_only=True` returns the last computed value of a formula rather than
    the formula text, because `=SUM(A1:A9)` is not the answer anyone wants.
    """
    from openpyxl import load_workbook

    pieces: list[str] = []
    total = 0

    try:
        # A handle, not a path -- for two separate reasons.
        #
        # First, openpyxl decides whether it can read a file by looking at the
        # *filename extension*, and NEXORA's storage keys deliberately have
        # none. Passing a path makes it refuse every upload we store.
        #
        # Second, read_only mode streams from the archive and keeps it open,
        # so owning the handle is what guarantees it is closed.
        with path.open("rb") as handle:
            workbook = load_workbook(handle, read_only=True, data_only=True)
            try:
                sheet_count = len(workbook.sheetnames)
                for sheet in workbook.worksheets:
                    pieces.append(f"# {sheet.title}")
                    for row in sheet.iter_rows(values_only=True):
                        cells = [
                            str(value).strip()
                            for value in row
                            if value not in (None, "")
                        ]
                        if not cells:
                            continue
                        line = " | ".join(cells)
                        pieces.append(line)
                        total += len(line) + 1
                        if total >= limit:
                            break
                    if total >= limit:
                        break
            finally:
                workbook.close()
    except ExtractionFailed:
        raise
    except Exception as error:  # noqa: BLE001 - openpyxl raises broadly
        logger.exception("XLSX extraction failed")
        raise ExtractionFailed(
            "This spreadsheet could not be read. It may be corrupt."
        ) from error

    text, truncated = _join_bounded(pieces, limit)
    return Extraction(text=text, page_count=sheet_count, truncated=truncated)


def validate_image(path: Path) -> tuple[int, int]:
    """Confirm an image really decodes, and return its dimensions.

    The magic bytes prove how a file *starts*; they prove nothing about the
    rest. Pillow parsing the header is what establishes it is a real image
    rather than a valid signature glued to arbitrary data.

    Note this deliberately does not call `Image.load()`, which would decode
    every pixel -- that is where a "decompression bomb" would cost real memory.
    Reading the header is enough to reject a fake, and Pillow raises on absurd
    declared dimensions by itself.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            image.verify()
            return image.size
    except UnidentifiedImageError as error:
        raise ExtractionFailed("This image could not be read.") from error
    except Image.DecompressionBombError as error:
        raise ExtractionFailed("This image is too large to process.") from error
    except Exception as error:  # noqa: BLE001 - Pillow raises broadly
        logger.exception("Image validation failed")
        raise ExtractionFailed("This image could not be read.") from error


_EXTRACTORS = {
    TXT: _extract_txt,
    CSV: _extract_csv,
    PDF: _extract_pdf,
    DOCX: _extract_docx,
    XLSX: _extract_xlsx,
}


def extract_sync(path: Path, kind: FileKind) -> Extraction:
    """Extract text from `path`, assuming it is `kind`. Blocking."""
    extractor = _EXTRACTORS.get(kind)
    if extractor is None:
        raise ExtractionFailed("Text cannot be extracted from this file type.")
    return extractor(path, get_settings().max_extracted_characters)


async def extract_text(path: Path, kind: FileKind) -> Extraction:
    """Extract text without blocking the event loop.

    `asyncio.to_thread` moves the parse onto a worker thread. Every parser here
    is synchronous and CPU-bound, and awaiting it on the event loop would stall
    every other request in the process -- most visibly the chat streams, which
    would stop mid-token while a PDF was read.

    This is not a substitute for a real job queue. Extraction still happens
    inside the request, so the user waits for it and a slow parse holds a
    connection open. A queue plus a worker is the right answer once uploads are
    large or frequent; the caps above are what make it acceptable until then.
    """
    return await asyncio.to_thread(extract_sync, path, kind)


async def validate_image_async(path: Path) -> tuple[int, int]:
    """`validate_image`, off the event loop."""
    return await asyncio.to_thread(validate_image, path)
