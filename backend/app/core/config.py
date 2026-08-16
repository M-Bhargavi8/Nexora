"""Application configuration.

Every setting comes from an environment variable, loaded from `backend/.env`
during development. Nothing is hard-coded and no secret has a usable default.

The important design choice here is **fail fast**: if a required setting is
missing or malformed, `Settings()` raises at startup rather than returning
`None` and causing a confusing crash later. Discovering a missing secret when
the process boots is cheap; discovering it at 3am in production is not.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/core/config.py -> app/core -> app -> backend
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed application settings loaded from the environment.

    Field names map to environment variables case-insensitively, so the
    attribute `secret_key` is populated from `SECRET_KEY`.
    """

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        # Ignore unrelated variables that happen to exist in the environment
        # instead of crashing on them.
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "NEXORA"
    app_env: Literal["development", "test", "production"] = "development"
    api_v1_prefix: str = "/api/v1"

    # --- Security ---
    # No default. The application cannot start without this, which is
    # deliberate: a default secret key is a security hole that ships quietly.
    secret_key: str = Field(
        min_length=32,
        description="Signing key for tokens/sessions. Generate a random one per environment.",
    )

    # --- Database ---
    # PostgresDsn validates the shape of the URL at startup, so a typo is
    # caught immediately instead of surfacing as a confusing driver error.
    database_url: PostgresDsn
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # Bounds the readiness probe. Kept below the 5s default timeout of most
    # load balancers so a slow database is reported, not mistaken for a dead
    # API process.
    db_connect_timeout_seconds: float = 3.0

    # --- CORS ---
    # Comma-separated list. Never "*" -- see cors_origin_list.
    cors_origins: str = "http://localhost:5173"

    # --- Logging ---
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse the comma-separated origins into a list.

        Env vars are always strings, so a list has to be parsed out. We keep
        the raw value a plain string rather than a JSON-encoded list because
        `CORS_ORIGINS=http://a,http://b` is far easier to write by hand and
        harder to get wrong.
        """
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the application settings, constructed once and reused.

    `lru_cache` makes this a singleton: the `.env` file is read and validated
    on first call only. Tests can call `get_settings.cache_clear()` to force a
    reload after changing environment variables.
    """
    return Settings()  # type: ignore[call-arg]  # values come from the environment
