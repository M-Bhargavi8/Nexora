"""The AI Gateway: chooses which provider the application talks to.

One function, `get_provider()`, is the single place that knows which adapter is
active. Route handlers call `get_provider().stream(...)` and never learn which
vendor answered -- that is the whole point of the abstraction.

Selection is by configuration, never by code change, so switching providers is
an environment variable and a restart.
"""

from __future__ import annotations

from functools import lru_cache

from app.ai.base import ModelProvider
from app.ai.providers.echo import EchoProvider
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_provider() -> ModelProvider:
    """Build (once) and return the configured model provider.

    Cached because constructing a provider opens an HTTP client with its own
    connection pool. Building one per request would leak sockets and lose the
    benefit of connection reuse -- the same reasoning as the database engine.

    The `anthropic` SDK is imported lazily, inside the branch that needs it, so
    that running with the echo provider does not require the package to be
    importable at all. That keeps the offline path genuinely offline.
    """
    settings = get_settings()
    provider_name = settings.ai_provider

    if provider_name == "echo":
        logger.warning(
            "Using the offline echo provider -- no real model will be called"
        )
        return EchoProvider(chunk_delay_seconds=settings.ai_echo_chunk_delay_seconds)

    if provider_name == "anthropic":
        from app.ai.providers.anthropic_provider import AnthropicProvider

        # Settings guarantees the key is present for this provider; see the
        # validator in app/core/config.py. Asserting here would be redundant.
        logger.info("Model provider: anthropic (%s)", settings.ai_model)
        return AnthropicProvider(
            api_key=settings.anthropic_api_key or "",
            model=settings.ai_model,
            max_tokens=settings.ai_max_tokens,
        )

    # Unreachable while `ai_provider` is a Literal -- Settings rejects anything
    # else before the application starts. Kept so that adding a name to the
    # Literal without adding a branch here fails loudly instead of silently
    # returning None.
    raise ValueError(f"Unknown AI provider: {provider_name!r}")
