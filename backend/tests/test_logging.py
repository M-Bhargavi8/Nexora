"""Tests for structured logging.

Logging is normally untested, which is exactly why it tends to break silently
and only be noticed during an incident -- the worst possible moment.
"""

from __future__ import annotations

import json
import logging

from app.core.logging import JsonFormatter, request_id_var


def make_record(message: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="nexora.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestJsonFormatter:
    def test_emits_valid_json(self) -> None:
        output = JsonFormatter().format(make_record())

        parsed = json.loads(output)  # raises if malformed
        assert parsed["message"] == "hello"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "nexora.test"
        assert "timestamp" in parsed

    def test_is_a_single_line(self) -> None:
        """Log aggregators split on newlines; a multi-line record would be
        ingested as several broken entries."""
        output = JsonFormatter().format(make_record("line one\nline two"))

        assert "\n" not in output

    def test_includes_the_request_id(self) -> None:
        output = JsonFormatter().format(make_record(request_id="req-42"))

        assert json.loads(output)["request_id"] == "req-42"

    def test_includes_structured_extra_fields(self) -> None:
        output = JsonFormatter().format(
            make_record(http_status=200, duration_ms=12.5)
        )

        parsed = json.loads(output)
        assert parsed["http_status"] == 200
        assert parsed["duration_ms"] == 12.5

    def test_serialises_unexpected_types_without_crashing(self) -> None:
        """A logger must never take the application down."""
        output = JsonFormatter().format(make_record(obj=object()))

        assert isinstance(json.loads(output)["obj"], str)


class TestRequestIdContextVar:
    def test_defaults_outside_a_request(self) -> None:
        assert request_id_var.get() == "-"

    def test_can_be_set_and_reset(self) -> None:
        token = request_id_var.set("req-1")
        assert request_id_var.get() == "req-1"

        request_id_var.reset(token)
        assert request_id_var.get() == "-"
