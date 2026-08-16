"""HTTP middleware.

Middleware wraps every request: code runs before the route handler, and again
after it produces a response. That makes it the right place for concerns that
apply to *all* endpoints -- here, request correlation and access logging.
"""

from __future__ import annotations

import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger, request_id_var

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Only these characters survive from a client-supplied request id.
_SAFE_REQUEST_ID = re.compile(r"[^A-Za-z0-9._-]")
_MAX_REQUEST_ID_LENGTH = 64


def _sanitize_request_id(raw: str | None) -> str:
    """Clean an incoming request id, or mint a new one.

    SECURITY: this value is written into log files. A client could otherwise
    send a header containing newlines and forge convincing fake log entries
    ("log injection"), or send a megabyte of text to bloat the logs. We strip
    everything outside a safe character set and cap the length.
    """
    if not raw:
        return uuid.uuid4().hex[:12]

    cleaned = _SAFE_REQUEST_ID.sub("", raw)[:_MAX_REQUEST_ID_LENGTH]
    return cleaned or uuid.uuid4().hex[:12]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, log the request, and time it.

    The id is echoed back in the response header so that a user reporting a
    problem can quote it and we can find the exact log lines for their
    request.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _sanitize_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = request_id_var.set(request_id)
        started = time.perf_counter()

        # Note the try/except/else/finally shape: `else` runs *before*
        # `finally`, so the log lines below still see the request id in the
        # context variable. Logging after the `finally` reset would tag every
        # access log line with "-" instead of the real id.
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            # exception() logs the traceback. The global exception handler in
            # main.py is what converts this into a 500 response.
            logger.exception(
                "%s %s failed after %.1fms",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise
        else:
            duration_ms = (time.perf_counter() - started) * 1000
            response.headers[REQUEST_ID_HEADER] = request_id

            # Log the path only -- never the query string, which can contain
            # tokens or personal data.
            logger.info(
                "%s %s -> %d (%.1fms)",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "http_status": response.status_code,
                    "duration_ms": round(duration_ms, 1),
                },
            )
            return response
        finally:
            request_id_var.reset(token)
