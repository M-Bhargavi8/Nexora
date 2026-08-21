"""Tests for the configuration system.

The point of these is to prove the app *refuses to start* on bad config,
rather than starting and misbehaving later.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID_SECRET = "a" * 32
VALID_DB_URL = "postgresql+asyncpg://user:pass@localhost:5432/nexora"

# Every variable Settings reads. Cleared before each test in this module.
NEXORA_ENV_VARS = (
    "APP_NAME",
    "APP_ENV",
    "API_V1_PREFIX",
    "SECRET_KEY",
    "DATABASE_URL",
    "DB_ECHO",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "DB_CONNECT_TIMEOUT_SECONDS",
    "CORS_ORIGINS",
    "LOG_LEVEL",
    "LOG_FORMAT",
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all NEXORA settings from the environment before each test.

    Without this, `conftest.py` (which sets SECRET_KEY and DATABASE_URL for
    the API tests) silently satisfies the very fields these tests are trying
    to prove are *required* -- so the tests would pass while asserting
    nothing. Config tests must control their entire input.

    monkeypatch restores the original environment automatically afterwards,
    so the API tests in other modules are unaffected.
    """
    for variable in NEXORA_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)


def build_settings(**overrides: object) -> Settings:
    """Construct Settings from explicit values only.

    `_env_file=None` disables reading `backend/.env`. Combined with the
    `isolated_environment` fixture above, this makes the tests depend on
    nothing outside this file -- no developer's local config can change the
    result.
    """
    values: dict[str, object] = {
        "secret_key": VALID_SECRET,
        "database_url": VALID_DB_URL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


class TestSecretKey:
    def test_rejects_a_short_secret_key(self) -> None:
        """SECURITY: a short key is guessable. Startup must fail loudly."""
        with pytest.raises(ValidationError) as exc_info:
            build_settings(secret_key="too-short")

        assert "secret_key" in str(exc_info.value)

    def test_accepts_a_sufficiently_long_secret_key(self) -> None:
        assert build_settings().secret_key == VALID_SECRET

    def test_secret_key_has_no_default(self) -> None:
        """A default secret is the kind that quietly reaches production."""
        with pytest.raises(ValidationError):
            Settings(_env_file=None, database_url=VALID_DB_URL)  # type: ignore[call-arg]


class TestDatabaseUrl:
    def test_rejects_a_malformed_url(self) -> None:
        with pytest.raises(ValidationError):
            build_settings(database_url="not-a-database-url")

    def test_database_url_is_required(self) -> None:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, secret_key=VALID_SECRET)  # type: ignore[call-arg]


class TestCorsOrigins:
    def test_parses_a_single_origin(self) -> None:
        settings = build_settings(cors_origins="http://localhost:5173")

        assert settings.cors_origin_list == ["http://localhost:5173"]

    def test_parses_multiple_origins_and_trims_whitespace(self) -> None:
        settings = build_settings(
            cors_origins="http://localhost:5173, https://nexora.app"
        )

        assert settings.cors_origin_list == [
            "http://localhost:5173",
            "https://nexora.app",
        ]

    def test_ignores_empty_entries(self) -> None:
        settings = build_settings(cors_origins="http://localhost:5173,,")

        assert settings.cors_origin_list == ["http://localhost:5173"]


class TestEnvironment:
    def test_rejects_an_unknown_environment(self) -> None:
        with pytest.raises(ValidationError):
            build_settings(app_env="staging")

    def test_is_production_flag(self) -> None:
        production = build_settings(app_env="production", session_cookie_secure=True)

        assert production.is_production is True
        assert build_settings(app_env="development").is_production is False

    def test_defaults_to_development(self) -> None:
        assert build_settings().app_env == "development"


class TestProductionHardening:
    def test_refuses_an_insecure_session_cookie_in_production(self) -> None:
        """SECURITY: without `secure`, the browser will send the session
        cookie over plain HTTP, where anyone on the network path can read it.

        Making the application refuse to boot turns a setting somebody forgot
        to flip during a deploy into an immediate, obvious failure rather than
        a silent vulnerability.
        """
        with pytest.raises(ValidationError) as exc_info:
            build_settings(app_env="production", session_cookie_secure=False)

        assert "SESSION_COOKIE_SECURE" in str(exc_info.value)

    def test_allows_a_secure_session_cookie_in_production(self) -> None:
        settings = build_settings(app_env="production", session_cookie_secure=True)

        assert settings.session_cookie_secure is True

    def test_allows_an_insecure_cookie_outside_production(self) -> None:
        """Local development runs on plain http://localhost, where a `secure`
        cookie would be silently dropped and login would appear broken."""
        settings = build_settings(app_env="development", session_cookie_secure=False)

        assert settings.session_cookie_secure is False


class TestSameSiteNoneRequiresSecure:
    def test_refuses_samesite_none_without_secure(self) -> None:
        """A correctness bug before it is a security one: every current browser
        silently discards a SameSite=None cookie that is not also Secure, so
        login returns 200 and then behaves as though it never happened."""
        with pytest.raises(ValidationError) as exc_info:
            build_settings(
                app_env="development",
                session_cookie_samesite="none",
                session_cookie_secure=False,
            )

        assert "SESSION_COOKIE_SAMESITE" in str(exc_info.value)

    def test_allows_samesite_none_with_secure(self) -> None:
        settings = build_settings(
            app_env="development",
            session_cookie_samesite="none",
            session_cookie_secure=True,
        )

        assert settings.session_cookie_samesite == "none"

    def test_lax_does_not_require_secure(self) -> None:
        """The default. Local development must keep working over plain HTTP."""
        settings = build_settings(
            app_env="development",
            session_cookie_samesite="lax",
            session_cookie_secure=False,
        )

        assert settings.session_cookie_samesite == "lax"
