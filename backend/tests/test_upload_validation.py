"""Tests for upload validation: filenames, magic bytes, and the checks between.

These are unit tests against `app/services/upload.py`, deliberately separate
from the HTTP tests. Every case here is an attack or a malformed input, and
naming them individually makes it obvious which defence a future change breaks.
"""

from __future__ import annotations

import pytest

from app.models.file import FILENAME_MAX_LENGTH
from app.services.upload import (
    CSV,
    DOCX,
    GIF,
    JPEG,
    PDF,
    PNG,
    TXT,
    WEBP,
    XLSX,
    UploadRejected,
    detect_kind,
    extension_of,
    sanitize_filename,
)

# Minimal but genuine file headers.
PDF_HEAD = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
ZIP_HEAD = b"PK\x03\x04\x14\x00\x06\x00"
PNG_HEAD = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
GIF_HEAD = b"GIF89a\x01\x00\x01\x00"
WEBP_HEAD = b"RIFF\x24\x00\x00\x00WEBPVP8 "


class TestExtensionOf:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("report.pdf", ".pdf"),
            ("REPORT.PDF", ".pdf"),
            ("notes.tar.gz", ".gz"),
            ("no-extension", ""),
            (".hidden", ".hidden"),
        ],
    )
    def test_reads_the_last_extension(self, filename: str, expected: str) -> None:
        assert extension_of(filename) == expected

    def test_a_double_extension_resolves_to_the_last_one(self) -> None:
        """`invoice.pdf.exe` is an executable. Reading the *first* extension is
        precisely how a double-extension trick gets past a check."""
        assert extension_of("invoice.pdf.exe") == ".exe"


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("../../etc/passwd", "passwd"),
            ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
            ("/absolute/path/report.pdf", "report.pdf"),
            ("C:\\Users\\ada\\report.pdf", "report.pdf"),
        ],
    )
    def test_strips_directory_components(self, raw: str, expected: str) -> None:
        """The real defence against traversal is that this value never becomes
        a path. This keeps the *label* from being misleading as well."""
        assert sanitize_filename(raw) == expected

    def test_removes_a_null_byte(self) -> None:
        # The classic truncation trick: a name that a C-string consumer reads
        # as "report.txt" while the extension check sees ".exe".
        assert sanitize_filename("report.txt\x00.exe") == "report.txt.exe"

    def test_removes_newlines(self) -> None:
        """A newline in a filename becomes a forged line in a log file."""
        assert sanitize_filename("report\n2026 final.pdf") == "report2026 final.pdf"

    @pytest.mark.parametrize("raw", ["", "   ", "...", "./", "\x00"])
    def test_falls_back_to_a_name_for_unusable_input(self, raw: str) -> None:
        assert sanitize_filename(raw) == "unnamed"

    def test_truncates_an_overlong_name_but_keeps_the_extension(self) -> None:
        result = sanitize_filename("x" * 400 + ".pdf")

        assert len(result) <= FILENAME_MAX_LENGTH
        # Still recognisable as a PDF, which a blind truncation would lose.
        assert result.endswith(".pdf")

    def test_leaves_an_ordinary_name_alone(self) -> None:
        assert sanitize_filename("Q3 report (final).pdf") == "Q3 report (final).pdf"


class TestDetectKind:
    @pytest.mark.parametrize(
        ("head", "filename", "expected"),
        [
            (PDF_HEAD, "report.pdf", PDF),
            (ZIP_HEAD, "report.docx", DOCX),
            (ZIP_HEAD, "data.xlsx", XLSX),
            (PNG_HEAD, "chart.png", PNG),
            (JPEG_HEAD, "photo.jpg", JPEG),
            (JPEG_HEAD, "photo.jpeg", JPEG),
            (GIF_HEAD, "animation.gif", GIF),
            (WEBP_HEAD, "image.webp", WEBP),
            (b"just some notes\n", "notes.txt", TXT),
            (b"# Heading\n\nText.", "readme.md", TXT),
            (b"name,age\nada,36\n", "people.csv", CSV),
        ],
    )
    def test_identifies_accepted_formats(
        self, head: bytes, filename: str, expected: object
    ) -> None:
        assert detect_kind(head, filename) is expected

    def test_content_wins_over_a_lying_extension(self) -> None:
        """A PNG renamed to .pdf must not be accepted as a PDF. The bytes are
        the fact; the extension is a claim."""
        with pytest.raises(UploadRejected, match="does not match"):
            detect_kind(PNG_HEAD, "definitely-a-report.pdf")

    def test_an_html_file_disguised_as_an_image_is_rejected(self) -> None:
        """The stored-XSS route: upload a page, get it served from our origin.
        Caught here by content, and again by the download headers."""
        with pytest.raises(UploadRejected):
            detect_kind(b"<!DOCTYPE html><script>alert(1)</script>", "logo.png")

    def test_an_executable_is_rejected(self) -> None:
        with pytest.raises(UploadRejected):
            detect_kind(b"MZ\x90\x00\x03\x00\x00\x00", "setup.exe")

    def test_an_elf_binary_is_rejected(self) -> None:
        with pytest.raises(UploadRejected):
            detect_kind(b"\x7fELF\x02\x01\x01\x00", "payload")

    def test_a_plain_zip_is_rejected(self) -> None:
        """`.zip` is not an accepted extension, so this never reaches the
        content check at all."""
        with pytest.raises(UploadRejected, match="not supported"):
            detect_kind(ZIP_HEAD, "archive.zip")

    def test_a_zip_with_no_extension_is_rejected(self) -> None:
        """A ZIP is only acceptable when it claims to be one of the two Office
        formats. With no extension it claims nothing, and the content alone
        cannot tell a .docx from any other archive."""
        with pytest.raises(UploadRejected, match="ZIP archive"):
            detect_kind(ZIP_HEAD, "archive")

    def test_binary_content_is_not_mistaken_for_text(self) -> None:
        """A NUL byte is the marker that separates text from binary."""
        with pytest.raises(UploadRejected):
            detect_kind(b"some text\x00\x01\x02\x03binary", "notes.txt")

    def test_invalid_utf8_is_not_mistaken_for_text(self) -> None:
        with pytest.raises(UploadRejected):
            detect_kind(b"\xff\xfe\x00\x01\x02 not utf-8", "notes.txt")

    def test_rejects_an_unsupported_extension_outright(self) -> None:
        with pytest.raises(UploadRejected, match="not supported"):
            detect_kind(b"anything at all", "script.sh")

    def test_a_pdf_signature_must_be_at_the_start(self) -> None:
        """Accepting the header anywhere would let another format be smuggled
        in front of it."""
        with pytest.raises(UploadRejected):
            detect_kind(b"<html>padding</html>%PDF-1.7", "report.pdf")

    @pytest.mark.parametrize(
        "filename", ["logo.png", "report.pdf", "sheet.xlsx", "photo.jpg"]
    )
    def test_text_content_cannot_claim_a_binary_extension(
        self, filename: str
    ) -> None:
        """Text is valid UTF-8 by definition, so without an explicit agreement
        check the text branch would accept anything wearing any extension."""
        with pytest.raises(UploadRejected, match="plain text"):
            detect_kind(b"just some text pretending", filename)

    def test_text_with_no_extension_is_accepted(self) -> None:
        """The rule is that content and extension must not *disagree*. A file
        with no extension makes no claim to disagree with -- a README is a
        real thing."""
        assert detect_kind(b"a readme with no extension", "README") is TXT

    def test_an_svg_is_rejected(self) -> None:
        """SVG is XML, and XML that a browser executes. It is text-like, so it
        is caught by the extension rule rather than by content."""
        with pytest.raises(UploadRejected):
            detect_kind(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "logo.svg")
