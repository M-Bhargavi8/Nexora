"""Response schemas for the health endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness: the process is running and able to answer HTTP."""

    status: Literal["ok"] = "ok"
    app: str = Field(description="Application name")
    version: str = Field(description="Application version")
    environment: str = Field(description="development | test | production")


class DependencyCheck(BaseModel):
    """The result of probing one external dependency."""

    name: str
    healthy: bool
    detail: str | None = Field(
        default=None,
        description="Short, non-sensitive reason when unhealthy",
    )


class ReadinessResponse(BaseModel):
    """Readiness: every dependency the app needs is actually reachable."""

    status: Literal["ready", "degraded"]
    checks: list[DependencyCheck]
