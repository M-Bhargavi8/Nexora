"""Deciding whether an uploaded file is acceptable, and what it actually is.

This module is the boundary where bytes NEXORA did not create enter the system.
Everything here exists because some piece of the request is attacker-controlled:

* the **filename** may contain `../`, a null byte, a drive letter, or be 4KB
  of Unicode chosen to break something downstream;
* the **Content-Type** header is written by the client and means nothing;
* the **length** the client advertises is a claim, not a fact;
* the **bytes** may be a ZIP bomb, or a `.png` that is really an HTML page.

So: the filename is never used to build a path, the declared type is ignored in
favour of the file's own magic bytes, the size is enforced while streaming
rather than trusted up front, and the extension must agree with the content.

Why not `python-magic`? It wraps the native `libmagic`, which is an extra
non-Python install on every developer machine and in every image, and it
identifies *hundreds* of formats NEXORA does not accept. The accepted set here
is small and fixed, so the signatures are checked directly -- fewer moving
parts, and the rule is readable in one place.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Final

from app.models.file import FILENAME_MAX_LENGTH


class UploadRejected(Exception):
    """An upload failed validation.

    Carries a message written for the person who uploaded the file: it says
    what is wrong and what would be acceptable, and never quotes internals.
    """


@dataclass(frozen=True)
class FileKind:
    """One accepted format."""

    # The canonical type NEXORA records. Chosen by us from the bytes, so it is
    # trustworthy in a way the client's Content-Type is not.
    content_type: str
    extensions: tuple[str, ...]
    # Whether text can be pulled out of it. False for images: NEXORA stores
    # them, but reading them needs a vision model, which is a later phase.
    extractable: bool


PDF: Final = FileKind("application/pdf", (".pdf",), True)
DOCX: Final = FileKind(
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    (".docx",),
    True,
)
XLSX: Final = FileKind(
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    (".xlsx",),
    True,
)
TXT: Final = FileKind("text/plain", (".txt", ".md"), True)
CSV: Final = FileKind("text/csv", (".csv",), True)
PNG: Final = FileKind("image/png", (".png",), False)
JPEG: Final = FileKind("image/jpeg", (".jpg", ".jpeg"), False)
GIF: Final = FileKind("image/gif", (".gif",), False)
WEBP: Final = FileKind("image/webp", (".webp",), False)

ACCEPTED_KINDS: Final[tuple[FileKind, ...]] = (
    PDF,
    DOCX,
    XLSX,
    TXT,
    CSV,
    PNG,
    JPEG,
    GIF,
    WEBP,
)

ACCEPTED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    extension for kind in ACCEPTED_KINDS for extension in kind.extensions
)

# How many bytes are enough to identify every format above. The longest
# signature check is WEBP, which needs 12.
SNIFF_BYTES: Final = 32

# Magic-byte signatures, longest first so a more specific one wins.
#
# DOCX and XLSX are both ZIP archives and share the same signature, so the
# bytes alone cannot tell them apart -- distinguishing them needs the extension
# (see `_resolve_zip_kind`). That is fine: the extension is only trusted to
# choose *between two formats the content already proved it could be*.
_ZIP_SIGNATURES: Final = (
    b"PK\x03\x04",  # ordinary archive
    b"PK\x05\x06",  # empty archive
    b"PK\x07\x08",  # spanned archive
)


def _looks_like(head: bytes, kind: FileKind) -> bool:
    """Whether `head` carries the signature of `kind`."""
    if kind is PDF:
        # The spec allows junk before the header, but every real producer puts
        # it first, and accepting it anywhere invites smuggling another format
        # in front of it.
        return head.startswith(b"%PDF-")
    if kind is PNG:
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if kind is JPEG:
        return head.startswith(b"\xff\xd8\xff")
    if kind is GIF:
        return head.startswith((b"GIF87a", b"GIF89a"))
    if kind is WEBP:
        # RIFF container: "RIFF", 4 length bytes, then "WEBP".
        return head.startswith(b"RIFF") and head[8:12] == b"WEBP"
    return False


def _is_zip(head: bytes) -> bool:
    return head.startswith(_ZIP_SIGNATURES)


def _resolve_zip_kind(extension: str) -> FileKind | None:
    """Decide which Office format a ZIP archive claims to be."""
    if extension in DOCX.extensions:
        return DOCX
    if extension in XLSX.extensions:
        return XLSX
    return None


def _is_probably_text(sample: bytes) -> bool:
    """Whether a sample decodes as UTF-8 and contains no control junk.

    Plain text has no magic number, so it is identified by exclusion: it is
    text if it decodes and does not contain the bytes that only appear in
    binary. A NUL in particular is the classic marker -- it is also how a
    `report.txt\\x00.exe` style filename trick shows up in content.
    """
    if b"\x00" in sample:
        return False
    try:
        decoded = sample.decode("utf-8")
    except UnicodeDecodeError:
        return False

    # Tab, newline and carriage return are legitimate; the rest of the C0
    # range is not something a text document contains.
    allowed_control = {"\t", "\n", "\r", "\f", "\v"}
    return not any(
        character < " " and character not in allowed_control for character in decoded
    )


def extension_of(filename: str) -> str:
    """The lowercased extension of a filename, including the dot.

    Deliberately takes the text after the **last** dot. `archive.tar.gz` is
    `.gz`, and -- the case that matters -- `invoice.pdf.exe` is `.exe`, not
    `.pdf`. Reading the first extension is how a double-extension trick gets
    through.
    """
    _, dot, suffix = filename.rpartition(".")
    return f".{suffix.lower()}" if dot else ""


def sanitize_filename(raw: str) -> str:
    """Reduce a client-supplied filename to something safe to store and show.

    This name is a **label**. It never becomes a path -- that is `storage_key`'s
    job -- so this is not the defence against traversal; the defence is that
    the value is not used for a path at all. What this does is stop the label
    itself from being a weapon: a name containing NUL, newlines or directory
    separators is confusing at best in a UI or a log line, and actively
    misleading at worst.
    """
    # NFC first: `a` + combining accent and `á` look identical but compare
    # differently, so normalising means one appearance has one representation.
    normalized = unicodedata.normalize("NFC", raw)

    # Take only the final path component, under both separators. A browser
    # sends a bare filename, but nothing forces a client to.
    for separator in ("\\", "/"):
        normalized = normalized.rpartition(separator)[2]

    # Strip control characters, including the NUL used to truncate names in C
    # string handling further down a stack.
    cleaned = "".join(
        character
        for character in normalized
        if character >= " " and character != "\x7f"
    ).strip()

    # `.` and `..` are not names. Leading dots hide files on Unix.
    cleaned = cleaned.lstrip(".").strip()

    if not cleaned:
        return "unnamed"

    if len(cleaned) > FILENAME_MAX_LENGTH:
        # Truncate the *stem* and keep the extension, so a long name stays
        # recognisable as the kind of file it is.
        extension = extension_of(cleaned)
        stem_budget = FILENAME_MAX_LENGTH - len(extension)
        cleaned = cleaned[:stem_budget] + extension

    return cleaned


def detect_kind(head: bytes, filename: str) -> FileKind:
    """Identify an upload from its leading bytes, cross-checked against its name.

    Raises `UploadRejected` when the file is not an accepted format, or when
    the content and the extension disagree.

    The order matters. Content is consulted **first**, so a `.png` that is
    really an HTML document is caught by its bytes. The extension is then
    required to agree, which stops the reverse trick: a genuine PDF uploaded as
    `notes.txt` and later served or opened as the wrong thing.
    """
    extension = extension_of(filename)

    if extension and extension not in ACCEPTED_EXTENSIONS:
        raise UploadRejected(
            f"{extension} files are not supported. Accepted types: "
            f"{', '.join(sorted(ACCEPTED_EXTENSIONS))}."
        )

    for kind in (PDF, PNG, JPEG, GIF, WEBP):
        if _looks_like(head, kind):
            if extension and extension not in kind.extensions:
                raise UploadRejected(
                    f"This file's contents are {kind.content_type}, which does "
                    f"not match its {extension} extension."
                )
            return kind

    if _is_zip(head):
        # Both Office formats are ZIPs. The extension picks between them; it
        # cannot introduce a format the content has not already established.
        kind = _resolve_zip_kind(extension)
        if kind is None:
            raise UploadRejected(
                "This looks like a ZIP archive. Only .docx and .xlsx Office "
                "files are supported."
            )
        return kind

    if _is_probably_text(head):
        # The content is text -- so the extension must not claim otherwise.
        #
        # Without this check the text branch becomes a hole straight through
        # the whole module: an HTML page named `logo.png`, or a script named
        # `report.pdf`, is valid UTF-8 and would be stored happily as
        # text/plain under a name promising something else. The binary branch
        # above enforces the same agreement; this is the other half of it.
        #
        # An *absent* extension is fine. The rule is that content and
        # extension must not disagree, and a file with no extension makes no
        # claim to disagree with.
        text_extensions = TXT.extensions + CSV.extensions
        if extension and extension not in text_extensions:
            raise UploadRejected(
                f"This file's contents are plain text, which does not match "
                f"its {extension} extension."
            )
        # `.md` is grouped with plain text: it is text, and rendering it is a
        # display decision rather than a parsing one.
        return CSV if extension in CSV.extensions else TXT

    raise UploadRejected(
        "This file type is not supported. Accepted types: "
        f"{', '.join(sorted(ACCEPTED_EXTENSIONS))}."
    )
