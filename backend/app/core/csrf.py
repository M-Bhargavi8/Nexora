"""Cross-Site Request Forgery protection.

**The attack.** You are logged in to NEXORA. You visit evil.example, whose
page silently submits a POST to our API. Because cookies are attached by the
browser automatically, that request arrives carrying your session cookie and
the server cannot tell it apart from one you meant to make.

**Why cookies specifically.** A token kept in JavaScript and sent in an
`Authorization` header is immune, because the attacker's page cannot read it.
Cookies are attached by the browser regardless of who triggered the request,
which is exactly what makes them convenient and exactly what makes CSRF
possible. Choosing cookie sessions means accepting this obligation.

**Two defences, layered:**

1. `SameSite=Lax` on the cookie (set in the auth routes). The browser then
   withholds the cookie on cross-site subrequests such as `fetch` from
   another origin. This alone stops most CSRF on modern browsers.
2. The origin check below. Defence in depth for older browsers and for any
   future endpoint where SameSite is relaxed.

Only state-changing methods are checked. GET must remain safe to call from
anywhere, which is also why no endpoint may change state on a GET.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.logging import get_logger

logger = get_logger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    """Reject state-changing requests that come from an unapproved origin."""

    def __init__(self, app, allowed_origins: list[str]) -> None:  # noqa: ANN001
        super().__init__(app)
        self._allowed = frozenset(allowed_origins)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method in SAFE_METHODS:
            return await call_next(request)

        origin = request.headers.get("origin")

        # No Origin header means the request did not come from a browser
        # page -- curl, a mobile app, a server-to-server call. CSRF is a
        # browser-only attack, and a browser cannot be made to omit this
        # header on a cross-origin request, so there is nothing to defend
        # against here. Rejecting these would break every non-browser client.
        if origin is None:
            return await call_next(request)

        if origin not in self._allowed:
            logger.warning(
                "Blocked a state-changing request from an unapproved origin",
                extra={"path": request.url.path, "method": request.method},
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Request origin is not allowed"},
            )

        return await call_next(request)
