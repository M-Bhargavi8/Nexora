"""The AI Gateway: chooses which provider the application talks to.

One function, `get_provider()`, is the single place that knows which adapter is
active. Route handlers call `get_provider().stream(...)` and never learn which
vendor answered -- that is the whole point of the abstraction.

Selection is by configuration, never by code change, so switching providers is
an environment variable and a restart.
"""

from __future__ import annotations

from functools import lru_cache

from app.ai.base import EmbeddingProvider, ImageReader, ModelProvider
from app.ai.providers.echo import EchoProvider
from app.ai.providers.hashing_embeddings import HashingEmbeddingProvider
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.chunk import EMBEDDING_DIMENSIONS

logger = get_logger(__name__)

#: Used when EMBEDDING_PROVIDER=gemini and EMBEDDING_MODEL is unset.
DEFAULT_GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"


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

    if provider_name == "gemini":
        from app.ai.providers.gemini_provider import GeminiProvider

        logger.info("Model provider: gemini (%s)", settings.ai_model)
        return GeminiProvider(
            api_key=settings.gemini_api_key or "",
            model=settings.ai_model,
            max_tokens=settings.ai_max_tokens,
        )

    # Unreachable while `ai_provider` is a Literal -- Settings rejects anything
    # else before the application starts. Kept so that adding a name to the
    # Literal without adding a branch here fails loudly instead of silently
    # returning None.
    raise ValueError(f"Unknown AI provider: {provider_name!r}")


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    """Build (once) and return the configured embedding provider.

    Selected separately from the chat provider, because embeddings and chat
    generation are bought separately -- Anthropic sells no embedding model, so
    a deployment running Anthropic for chat needs something else here.

    Adding a hosted provider is a new class implementing `EmbeddingProvider`,
    a name in the `embedding_provider` Literal, and a branch below. Nothing
    above this function changes.

    Switching providers or models does not corrupt retrieval: every chunk
    records the `identity` of the model that embedded it, and search compares
    only against chunks from the active one. Old chunks simply stop matching
    until `python -m scripts.reindex` re-embeds them.
    """
    settings = get_settings()
    provider_name = settings.embedding_provider

    provider: EmbeddingProvider
    if provider_name == "hashing":
        logger.warning(
            "Using the offline hashing embedding provider -- retrieval is "
            "lexical (shared words) and not semantic"
        )
        provider = HashingEmbeddingProvider(dimensions=EMBEDDING_DIMENSIONS)
    elif provider_name == "gemini":
        from app.ai.providers.gemini_provider import GeminiEmbeddingProvider

        # The width is the storage column's, not a setting: the column is a
        # fixed vector(n), so a provider must produce exactly that.
        provider = GeminiEmbeddingProvider(
            api_key=settings.gemini_api_key or "",
            model=settings.embedding_model or DEFAULT_GEMINI_EMBEDDING_MODEL,
            dimensions=EMBEDDING_DIMENSIONS,
            timeout_seconds=settings.embedding_timeout_seconds,
        )
    else:
        raise ValueError(f"Unknown embedding provider: {provider_name!r}")

    logger.info("Embedding provider: %s", provider.identity)
    return provider


@lru_cache(maxsize=1)
def get_image_reader() -> ImageReader | None:
    """The configured image reader, or None when images are stored unread."""
    settings = get_settings()
    if settings.image_reader == "gemini":
        from app.ai.providers.gemini_provider import GeminiImageReader

        logger.info("Image reader: gemini (%s)", settings.image_reader_model)
        return GeminiImageReader(
            api_key=settings.gemini_api_key or "",
            model=settings.image_reader_model,
            timeout_seconds=settings.image_reader_timeout_seconds,
        )
    return None
