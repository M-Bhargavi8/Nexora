"""Structured logging setup.

Two output formats, chosen by the LOG_FORMAT setting:

* ``console`` -- human-readable, for local development.
* ``json``    -- one JSON object per line, for production. Log aggregators
                 (CloudWatch, Loki, Datadog) can index JSON fields; they
                 cannot reliably parse free-form text.

Every log line carries a **request id** so that all the lines produced while
handling one HTTP request can be grouped together. Without this, concurrent
requests interleave in the log and become impossible to follow.

The request id travels via a ``ContextVar``, which is the async-aware
equivalent of thread-local storage: each concurrent task sees its own value,
so we do not have to thread a logger argument through every function.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# Default "-" means "logged outside of any request" (e.g. startup messages).
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes present on every LogRecord. Anything *not* in this set was added
# by the caller via `extra={...}` and should be included in JSON output.
_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
) | {"asctime", "message", "request_id", "taskName"}


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every log record.

    A logging *filter* is used rather than a custom logger because it applies
    to records from third-party libraries (uvicorn, SQLAlchemy) too, not just
    our own code.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True  # never actually filters anything out


class JsonFormatter(logging.Formatter):
    """Render a log record as a single line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Include structured context passed as logger.info("...", extra={...}).
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str)


def configure_logging(log_level: str = "INFO", log_format: str = "console") -> None:
    """Configure the root logger. Safe to call more than once.

    We configure the *root* logger and let messages propagate up to it, so
    that libraries logging to their own named loggers are captured with the
    same formatting and request id.
    """
    formatter: logging.Formatter
    if log_format == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    # Replace existing handlers so repeated calls (e.g. uvicorn reload) do not
    # duplicate every log line.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level.upper())

    # uvicorn installs its own handlers; clearing them and letting the records
    # propagate to root gives us one consistent format across the whole app.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger. Use ``get_logger(__name__)``."""
    return logging.getLogger(name)
