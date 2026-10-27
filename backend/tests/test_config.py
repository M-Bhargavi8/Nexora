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
    "CHAT_RATE_LIMIT_ATTEMPTS",
    "CHAT_RATE_LIMIT_WINDOW_SECONDS",
    "AI_PROVIDER",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "AI_MODEL",
    "AI_MAX_TOKENS",
    "AI_ECHO_CHUNK_DELAY_SECONDS",
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


def build_production_settings(**overrides: object) -> Settings:
    """Settings that satisfy every production validator.

    Production insists on more than one thing, and that list grows with each
    phase. Centralising the valid baseline here means a new production rule
    breaks this one helper, rather than every test that merely happens to
    build production settings for an unrelated reason.
    """
    values: dict[str, object] = {
        "app_env": "production",
        "session_cookie_secure": True,
        "ai_provider": "anthropic",
        "anthropic_api_key": "sk-not-a-real-key",
        # Production refuses the lexical hashing embeddings.
        "embedding_provider": "gemini",
        "gemini_api_key": "not-a-real-key",
    }
    values.update(overrides)
    return build_settings(**values)


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
        production = build_production_settings()

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
            build_production_settings(session_cookie_secure=False)

        assert "SESSION_COOKIE_SECURE" in str(exc_info.value)

    def test_allows_a_secure_session_cookie_in_production(self) -> None:
        settings = build_production_settings()

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


class TestModelProviderConfig:
    def test_anthropic_without_an_api_key_refuses_to_start(self) -> None:
        """Caught at boot as one clear log line, rather than as a 500 in the
        user's face on the first chat message."""
        with pytest.raises(ValidationError) as exc_info:
            build_settings(ai_provider="anthropic", anthropic_api_key=None)

        assert "ANTHROPIC_API_KEY" in str(exc_info.value)

    def test_anthropic_with_an_api_key_is_accepted(self) -> None:
        settings = build_settings(
            ai_provider="anthropic", anthropic_api_key="sk-not-a-real-key"
        )

        assert settings.ai_provider == "anthropic"

    def test_gemini_without_an_api_key_refuses_to_start(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            build_settings(
                ai_provider="gemini",
                gemini_api_key=None,
                ai_model="gemini-3.7-flash",
            )

        assert "GEMINI_API_KEY" in str(exc_info.value)

    def test_gemini_with_an_api_key_is_accepted(self) -> None:
        settings = build_settings(
            ai_provider="gemini",
            gemini_api_key="not-a-real-key",
            ai_model="gemini-3.7-flash",
        )

        assert settings.ai_provider == "gemini"

    def test_gemini_with_a_claude_model_refuses_to_start(self) -> None:
        """`ai_model` is shared across providers and defaults to a Claude id.
        Switching the provider and forgetting it would send "claude-opus-5" to
        Google and get back a confusing 404 from a service that has never
        heard of it."""
        with pytest.raises(ValidationError) as exc_info:
            build_settings(
                ai_provider="gemini",
                gemini_api_key="not-a-real-key",
                ai_model="claude-opus-5",
            )

        assert "AI_MODEL" in str(exc_info.value)

    def test_anthropic_with_a_gemini_model_refuses_to_start(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            build_settings(
                ai_provider="anthropic",
                anthropic_api_key="sk-not-a-real-key",
                ai_model="gemini-3.7-flash",
            )

        assert "AI_MODEL" in str(exc_info.value)

    def test_an_unrecognised_model_name_is_allowed(self) -> None:
        """A prefix check, not a catalogue. Model names change too often to
        hard-code a list that would reject something released next month."""
        settings = build_settings(
            ai_provider="gemini",
            gemini_api_key="not-a-real-key",
            ai_model="gemini-4-something-unreleased",
        )

        assert settings.ai_model == "gemini-4-something-unreleased"

    def test_the_fake_provider_is_refused_in_production(self) -> None:
        """SECURITY: the echo provider returns canned text. Shipping it would
        mean users reading invented answers believing a model wrote them."""
        with pytest.raises(ValidationError) as exc_info:
            build_production_settings(ai_provider="echo")

        assert "AI_PROVIDER" in str(exc_info.value)

    def test_the_fake_provider_is_allowed_in_development(self) -> None:
        """It is the default, so that the app still starts with no credentials."""
        assert build_settings(app_env="development").ai_provider == "echo"

    def test_a_token_cap_is_always_set(self) -> None:
        """An unbounded reply length is an unbounded bill."""
        assert build_settings().ai_max_tokens > 0
