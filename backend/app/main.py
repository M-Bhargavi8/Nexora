"""FastAPI application factory and startup wiring.

Built as a factory function (`create_app`) rather than a module-level `app`
object so that tests can build a fresh, independently-configured application
instead of importing a global that was already configured at import time.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.csrf import CSRFProtectionMiddleware
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.db.session import dispose_engine
from app.services.auth import warm_timing_defence

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run startup and shutdown logic.

    Everything before `yield` runs once at startup; everything after runs at
    shutdown. This replaces the deprecated @app.on_event handlers and, unlike
    them, guarantees cleanup even when startup partially fails.

    Note we do *not* connect to the database here. A hard dependency on the
    database at startup means the API cannot boot at all during a database
    blip -- it could not even serve its own health endpoint to report the
    problem. Instead the pool connects lazily and `/health/ready` reports the
    truth.
    """
    settings: Settings = get_settings()
    logger.info(
        "%s v%s starting in %s mode",
        settings.app_name,
        __version__,
        settings.app_env,
    )

    # Precompute the Argon2 hash that equalises response time for logins with
    # an unknown email. Done here rather than at import so that alembic, the
    # test suite and any CLI tool do not each pay ~50ms for it -- and done at
    # startup rather than lazily so the *first* such login is not the one
    # request that stands out as slow.
    warm_timing_defence()

    yield
    await dispose_engine()
    logger.info("%s shut down cleanly", settings.app_name)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(log_level=settings.log_level, log_format=settings.log_format)

    app = FastAPI(
        title=settings.app_name,
        description="NEXORA — Your Intelligent AI Workspace",
        version=__version__,
        lifespan=lifespan,
        # SECURITY: interactive docs map the entire attack surface, so they
        # are disabled in production and available everywhere else.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # --- Middleware ---
    # Order matters: middleware added last runs first (outermost). Adding the
    # request-context middleware after CORS means it wraps CORS, so even
    # rejected pre-flight requests get logged with an id.
    app.add_middleware(
        CORSMiddleware,
        # SECURITY: an explicit allow-list, never ["*"]. With
        # allow_credentials=True the browser refuses "*" anyway, and a
        # wildcard would let any site call this API with the user's cookies.
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    # Starlette's add_middleware *prepends*, so the last one added ends up
    # outermost. The real execution order is therefore:
    #
    #     RequestContext -> CSRF -> CORS -> route handler
    #
    # CSRF runs *before* CORS, not inside it. That is fine, and marginally
    # safer, since a forged cross-origin POST is rejected earlier. The one
    # consequence worth knowing when debugging: a CSRF 403 never travels back
    # out through CORSMiddleware, so it carries no Access-Control-Allow-Origin
    # header and the browser reports it as a CORS failure instead of showing
    # the 403 body.
    #
    # This is the second layer behind the cookie's SameSite attribute, not the
    # first.
    app.add_middleware(
        CSRFProtectionMiddleware, allowed_origins=settings.cors_origin_list
    )
    app.add_middleware(RequestContextMiddleware)

    # --- Routes ---
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    # --- Error handling ---
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Convert any uncaught exception into a generic 500.

        SECURITY: the traceback is logged server-side (by the middleware) but
        never sent to the client. Stack traces reveal file paths, library
        versions, and sometimes credentials -- valuable reconnaissance for an
        attacker.
        """
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    return app


# The application instance used by uvicorn: `uvicorn app.main:app`
app = create_app()
