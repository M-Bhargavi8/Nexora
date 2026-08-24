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

from pydantic import Field, PostgresDsn, model_validator
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

    # --- Sessions / authentication ---
    session_cookie_name: str = "nexora_session"
    session_lifetime_days: int = 7
    # SECURITY: `secure=True` tells the browser to send the cookie only over
    # HTTPS. It must be False in local development (http://localhost) or the
    # browser silently drops the cookie and login appears to "just not work".
    # It MUST be True in production -- enforced by the validator below.
    session_cookie_secure: bool = False
    # "lax" sends the cookie on top-level navigations but not on cross-site
    # subrequests, which is the primary CSRF defence.
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # --- Rate limiting ---
    # Applied to authentication endpoints to slow password brute-forcing.
    #
    # Two dimensions, because they stop different attacks: the per-IP limit
    # stops one machine guessing many passwords, the per-account limit stops a
    # botnet guessing one account's password from many addresses.
    auth_rate_limit_attempts: int = 10
    auth_rate_limit_window_seconds: int = 60
    # Stricter than the per-IP limit: a real person rarely mistypes their own
    # password five times in a minute, while an attacker does nothing else.
    auth_account_rate_limit_attempts: int = 5

    # --- Chat rate limiting ---
    # Keyed by user, not by IP: this endpoint is authenticated, and the cost
    # it incurs is attributable to an account rather than an address. An
    # office full of people behind one NAT should not share a model budget.
    #
    # SECURITY/COST: unlike the auth limiter, which exists to slow password
    # guessing, this one exists because every request spends money. An
    # unlimited paid endpoint is a financial denial of service.
    chat_rate_limit_attempts: int = 30
    chat_rate_limit_window_seconds: int = 60

    # --- AI ---
    # Which adapter the gateway builds. Changing providers is this value plus
    # a restart -- never a code change. See app/ai/gateway.py.
    #
    # Defaults to "echo" so the application still starts on a machine with no
    # model credentials, which keeps Phases 1-2 runnable and lets frontend
    # work happen without spending money. A validator below refuses "echo" in
    # production, so the safe default cannot silently ship.
    ai_provider: Literal["anthropic", "echo"] = "echo"
    # SECURITY: read from the environment, never committed, and never sent to
    # the browser. Every model call is made server-side precisely so this
    # value stays on the server.
    anthropic_api_key: str | None = None
    ai_model: str = "claude-opus-5"
    # Caps the reply length, which caps the cost of any single request. An
    # unbounded max_tokens is a way for one user to spend a lot of money.
    ai_max_tokens: int = 4096
    # Dev-only knob: makes the echo provider pause between chunks so the
    # streaming UI can be watched arriving piece by piece. 0 in tests.
    ai_echo_chunk_delay_seconds: float = 0.0

    # --- File uploads ---
    # Where uploaded bytes are written. Kept outside the source tree so an
    # uploaded file can never be picked up as application code, and so the
    # directory can be a mounted volume in production.
    upload_dir: Path = BACKEND_DIR / "var" / "uploads"
    # SECURITY/COST: the hard ceiling on one upload. Enforced while streaming,
    # not by trusting the Content-Length header a client sends. 20MB is
    # comfortable for documents and small enough that a handful of concurrent
    # uploads cannot exhaust disk or memory.
    max_upload_bytes: int = 20 * 1024 * 1024
    # DOCX and XLSX are ZIP archives, so a small upload can decompress to
    # something enormous -- a "zip bomb". Extraction stops once this much text
    # has been produced, which bounds both memory and the eventual prompt.
    max_extracted_characters: int = 1_000_000
    # Bounds how long parsing one PDF can take. A malformed or hostile file
    # with a huge page count is a CPU denial of service otherwise.
    max_pdf_pages: int = 500
    # Uploads are authenticated, so this is keyed by user like the chat limit.
    upload_rate_limit_attempts: int = 20
    upload_rate_limit_window_seconds: int = 60

    # --- Retrieval (RAG) ---
    # Which embedding adapter the gateway builds. Separate from `ai_provider`
    # because embeddings and chat generation are bought separately.
    #
    # WARNING: changing this invalidates every stored vector. Embeddings from
    # two models are not comparable, and searching across them returns
    # confident nonsense rather than an error. Re-embed everything.
    embedding_provider: Literal["hashing"] = "hashing"

    # Target size of one chunk, in characters.
    #
    # The trade-off, which has no universally right answer: small chunks
    # retrieve precisely but arrive without context, so a passage may be
    # returned without the sentence that gave it meaning. Large chunks carry
    # their context but dilute the match and cost more tokens per hit. ~1200
    # characters is roughly a substantial paragraph.
    chunk_size_characters: int = 1200
    # How much of the previous chunk each one repeats. Overlap exists because a
    # fact split across a boundary would otherwise be retrievable from neither
    # half -- the sentence that names the subject lands in one chunk and the
    # figure in the next.
    chunk_overlap_characters: int = 200
    # How many chunks a search returns. Each one is sent to the model, so this
    # is directly a cost and a context-window decision, not just a quality one.
    retrieval_top_k: int = 5
    # Cosine similarity below this is treated as "no match".
    #
    # Without a floor, retrieval *always* returns its k nearest chunks, however
    # irrelevant -- ask about the weather with a tax return uploaded and you
    # get five passages about tax, presented as though they were relevant.
    # Returning nothing is the honest answer to a question the documents do not
    # address.
    retrieval_min_similarity: float = 0.15

    # --- CORS ---
    # Comma-separated list. Never "*" -- see cors_origin_list.
    cors_origins: str = "http://localhost:5173"

    # --- Logging ---
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    @model_validator(mode="after")
    def enforce_production_hardening(self) -> Settings:
        """Refuse to start with insecure settings in production.

        A configuration flag that is safe in development and dangerous in
        production is exactly the kind of thing that gets forgotten during a
        deploy. Making the application refuse to boot turns a silent security
        hole into an obvious, immediate failure.
        """
        if self.app_env == "production" and not self.session_cookie_secure:
            raise ValueError(
                "SESSION_COOKIE_SECURE must be true in production, otherwise "
                "the session cookie can be sent over plain HTTP and stolen."
            )
        return self

    @model_validator(mode="after")
    def enforce_model_provider_is_usable(self) -> Settings:
        """Refuse to start if the selected provider cannot actually work.

        Both checks fail at startup rather than on the first chat request. A
        missing API key discovered at boot is one clear log line; discovered
        on the first user message it is a 500 in someone's face, and the cause
        is several layers away from the symptom.
        """
        if self.ai_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError(
                "AI_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set."
            )

        if self.app_env == "production" and self.ai_provider == "echo":
            raise ValueError(
                "AI_PROVIDER=echo is a fake offline provider and must never "
                "run in production -- it returns canned text, not model output."
            )

        # NOTE: there is deliberately no production guard on
        # `embedding_provider`, unlike the one above for `ai_provider`.
        #
        # The echo chat provider returns *fabricated* text, and a real
        # alternative exists, so refusing to boot is right. The hashing
        # embedding provider returns genuine -- merely weak -- retrieval, and
        # right now it is the only implementation there is. Failing startup
        # would make the whole application unbootable in production, including
        # the chat that has nothing to do with retrieval.
        #
        # It warns loudly instead, in `get_embedding_provider()`. A guard
        # belongs here the moment a second provider exists to switch to.
        return self

    @model_validator(mode="after")
    def enforce_samesite_none_requires_secure(self) -> Settings:
        """Reject SameSite=None without Secure, in every environment.

        This is a correctness bug before it is a security one: every current
        browser *silently discards* a `SameSite=None` cookie that is not also
        `Secure`. The symptom is a login that returns 200 and then behaves as
        though it never happened, which is miserable to debug. Failing at
        startup turns it into one clear message.

        It is a security issue too -- SameSite=None switches off the primary
        CSRF defence, so the cookie had better not also travel over plain HTTP.
        """
        if self.session_cookie_samesite == "none" and not self.session_cookie_secure:
            raise ValueError(
                "SESSION_COOKIE_SAMESITE=none requires SESSION_COOKIE_SECURE=true. "
                "Browsers silently drop a SameSite=None cookie that is not Secure."
            )
        return self

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
