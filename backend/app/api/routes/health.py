"""Health endpoints.

Two endpoints, because they answer two genuinely different questions:

``GET /health``       -- *liveness*. "Is this process alive?" It touches no
                         dependency and is always fast. An orchestrator that
                         gets a failure here should restart the process.

``GET /health/ready`` -- *readiness*. "Can this process actually serve
                         traffic?" It checks the database. A failure means
                         stop sending requests here, but do **not** restart --
                         restarting the API will not fix a down database.

Collapsing these into one endpoint is a common mistake: a brief database
outage then triggers a restart loop of perfectly healthy API processes.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app import __version__
from app.core.config import get_settings
from app.db.session import check_database_connection
from app.schemas.health import DependencyCheck, HealthResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["Health"])


@router.get(
    "",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 whenever the process is running. Checks no dependencies.",
)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        app=settings.app_name,
        version=__version__,
        environment=settings.app_env,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Returns 200 when every dependency is reachable, or 503 when one is "
        "not. Currently checks the PostgreSQL connection."
    ),
    responses={503: {"description": "One or more dependencies are unavailable"}},
)
async def readiness(response: Response) -> ReadinessResponse:
    db_healthy, db_detail = await check_database_connection()

    checks = [DependencyCheck(name="database", healthy=db_healthy, detail=db_detail)]
    all_healthy = all(check.healthy for check in checks)

    if not all_healthy:
        # Set the status code on the injected Response rather than raising
        # HTTPException, so the body keeps the structured per-dependency
        # detail instead of collapsing to {"detail": "..."}.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if all_healthy else "degraded",
        checks=checks,
    )
