"""Unit tests for request-id sanitization.

These call the function directly rather than going through HTTP, because HTTP
clients refuse to send some of the malicious values we need to test -- the
dangerous input would never reach the application to be exercised.
"""

from __future__ import annotations

import pytest

from app.core.middleware import _MAX_REQUEST_ID_LENGTH, _sanitize_request_id


class TestSanitizeRequestId:
    def test_generates_an_id_when_header_is_absent(self) -> None:
        generated = _sanitize_request_id(None)

        assert generated
        assert len(generated) == 12

    def test_generates_a_unique_id_each_time(self) -> None:
        assert _sanitize_request_id(None) != _sanitize_request_id(None)

    def test_generates_an_id_when_header_is_empty(self) -> None:
        assert len(_sanitize_request_id("")) == 12

    def test_preserves_a_well_formed_id(self) -> None:
        assert _sanitize_request_id("trace-abc_123.4") == "trace-abc_123.4"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # SECURITY: the core log-injection case -- a newline would let an
            # attacker append a convincing fake line to the log file.
            ("abc\r\nFAKE LOG ENTRY", "abcFAKELOGENTRY"),
            ("abc\ndef", "abcdef"),
            ("<script>alert(1)</script>", "scriptalert1script"),
            ("a b c", "abc"),
            ("id;DROP TABLE users", "idDROPTABLEusers"),
        ],
    )
    def test_strips_dangerous_characters(self, raw: str, expected: str) -> None:
        assert _sanitize_request_id(raw) == expected

    def test_truncates_an_overlong_id(self) -> None:
        result = _sanitize_request_id("x" * 1000)

        assert len(result) == _MAX_REQUEST_ID_LENGTH

    def test_falls_back_when_every_character_is_stripped(self) -> None:
        """An id of only illegal characters must still yield a usable id
        rather than an empty string."""
        result = _sanitize_request_id("!!!###")

        assert len(result) == 12
