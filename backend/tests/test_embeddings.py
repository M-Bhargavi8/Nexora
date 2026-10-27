"""Tests for embedding providers, their identity, and how they are selected.

The Gemini adapter is driven by a **fake SDK client**: no network, no API key,
no cost. What is under test is the request we build, the checks we apply to
what comes back, and the errors we map -- not Google's model. Whether the real
model retrieves well is a question for the live API and the evaluation set.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterator
from typing import Any

import pytest
from google.genai import errors as genai_errors
from pydantic import ValidationError

from app.ai.base import (
    ModelAuthError,
    ModelProviderError,
    ModelRateLimitError,
    ModelUnavailableError,
)
from app.ai.gateway import DEFAULT_GEMINI_EMBEDDING_MODEL, get_embedding_provider
from app.ai.providers.gemini_provider import EMBED_BATCH_SIZE, GeminiEmbeddingProvider
from app.ai.providers.hashing_embeddings import HashingEmbeddingProvider
from app.core.config import Settings
from app.models.chunk import EMBEDDING_DIMENSIONS

VALID_SECRET = "a" * 32
VALID_DB_URL = "postgresql+asyncpg://user:pass@localhost:5432/nexora"
DIMS = 8


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "secret_key": VALID_SECRET,
        "database_url": VALID_DB_URL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def clear_provider_cache() -> Iterator[None]:
    get_embedding_provider.cache_clear()
    yield
    get_embedding_provider.cache_clear()


# --- a fake standing in for genai.Client ------------------------------------


class _FakeEmbedding:
    def __init__(self, values: list[float]) -> None:
        self.values = values


class _FakeResponse:
    def __init__(self, embeddings: list[_FakeEmbedding]) -> None:
        self.embeddings = embeddings


class _FakeModels:
    def __init__(self, client: FakeClient) -> None:
        self._client = client

    async def embed_content(self, *, model: str, contents: list[str], config: Any) -> _FakeResponse:
        self._client.calls.append({"model": model, "contents": contents, "config": config})
        if self._client.delay:
            await asyncio.sleep(self._client.delay)
        if self._client.error is not None:
            raise self._client.error
        width = self._client.width
        count = len(contents) + self._client.count_skew
        # Not unit length on purpose, as Google's shortened vectors are not.
        return _FakeResponse(
            [_FakeEmbedding([float(i + 1)] * width) for i in range(count)]
        )


class _FakeAio:
    def __init__(self, client: FakeClient) -> None:
        self.models = _FakeModels(client)


class FakeClient:
    def __init__(
        self,
        *,
        width: int = DIMS,
        error: Exception | None = None,
        count_skew: int = 0,
        delay: float = 0.0,
    ) -> None:
        self.width = width
        self.error = error
        self.count_skew = count_skew
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self.aio = _FakeAio(self)


def build_provider(
    client: FakeClient | None = None, *, timeout: float = 5.0
) -> tuple[GeminiEmbeddingProvider, FakeClient]:
    client = client or FakeClient()
    provider = GeminiEmbeddingProvider(
        api_key="not-a-real-key",
        model="gemini-embedding-001",
        dimensions=DIMS,
        timeout_seconds=timeout,
        client=client,
    )
    return provider, client


def _api_error(cls: type, code: int) -> Exception:
    return cls(code, {"error": {"message": "provider said something", "status": "X"}})


# --- identity ---------------------------------------------------------------


class TestIdentity:
    def test_hashing_identity_names_algorithm_version_and_width(self) -> None:
        """Migration 0005 back-fills existing chunks with exactly this string.
        If it changes, every stored chunk silently becomes unsearchable."""
        assert HashingEmbeddingProvider().identity == "hashing:hashing-v1:512"

    def test_gemini_identity_includes_model_and_width(self) -> None:
        provider, _ = build_provider()
        assert provider.identity == f"gemini:gemini-embedding-001:{DIMS}"

    def test_two_models_never_share_an_identity(self) -> None:
        provider, _ = build_provider()
        assert provider.identity != HashingEmbeddingProvider().identity


# --- the Gemini adapter ------------------------------------------------------


class TestGeminiEmbeddings:
    async def test_returns_one_vector_per_text_in_order(self) -> None:
        provider, _ = build_provider()
        vectors = await provider.embed(["a", "b", "c"])
        assert len(vectors) == 3
        assert all(len(v) == DIMS for v in vectors)

    async def test_vectors_are_unit_length(self) -> None:
        provider, _ = build_provider()
        [vector] = await provider.embed(["a"])
        assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0)

    async def test_asks_for_the_column_width(self) -> None:
        provider, client = build_provider()
        await provider.embed(["a"])
        assert client.calls[0]["config"].output_dimensionality == DIMS
        assert client.calls[0]["model"] == "gemini-embedding-001"

    @pytest.mark.parametrize(
        ("purpose", "task"),
        [("document", "RETRIEVAL_DOCUMENT"), ("query", "RETRIEVAL_QUERY")],
    )
    async def test_tells_the_model_which_side_of_the_search(
        self, purpose: str, task: str
    ) -> None:
        provider, client = build_provider()
        await provider.embed(["a"], purpose=purpose)  # type: ignore[arg-type]
        assert client.calls[0]["config"].task_type == task

    async def test_defaults_to_document(self) -> None:
        provider, client = build_provider()
        await provider.embed(["a"])
        assert client.calls[0]["config"].task_type == "RETRIEVAL_DOCUMENT"

    async def test_large_inputs_are_split_into_batches(self) -> None:
        provider, client = build_provider()
        texts = [f"t{i}" for i in range(EMBED_BATCH_SIZE * 2 + 3)]

        vectors = await provider.embed(texts)

        assert len(vectors) == len(texts)
        assert [len(c["contents"]) for c in client.calls] == [
            EMBED_BATCH_SIZE,
            EMBED_BATCH_SIZE,
            3,
        ]
        # Order preserved across batches.
        assert [t for c in client.calls for t in c["contents"]] == texts

    async def test_wrong_width_fails_loudly(self) -> None:
        """A model that ignores output_dimensionality must not write vectors
        the column cannot hold or the index would compare wrongly."""
        provider, _ = build_provider(FakeClient(width=DIMS * 2))
        with pytest.raises(ModelProviderError, match="dimensions"):
            await provider.embed(["a"])

    async def test_a_dropped_vector_fails_loudly(self) -> None:
        """Positional pairing: a missing vector would shift every later chunk
        onto another chunk's meaning."""
        provider, _ = build_provider(FakeClient(count_skew=-1))
        with pytest.raises(ModelProviderError):
            await provider.embed(["a", "b"])

    async def test_a_hung_call_times_out(self) -> None:
        provider, _ = build_provider(FakeClient(delay=1.0), timeout=0.05)
        with pytest.raises(ModelUnavailableError, match="timed out"):
            await provider.embed(["a"])


class TestGeminiEmbeddingErrors:
    @pytest.mark.parametrize("code", [401, 403])
    async def test_credentials_become_an_auth_error(self, code: int) -> None:
        provider, _ = build_provider(
            FakeClient(error=_api_error(genai_errors.ClientError, code))
        )
        with pytest.raises(ModelAuthError):
            await provider.embed(["a"])

    async def test_429_becomes_a_rate_limit_error(self) -> None:
        provider, _ = build_provider(
            FakeClient(error=_api_error(genai_errors.ClientError, 429))
        )
        with pytest.raises(ModelRateLimitError):
            await provider.embed(["a"])

    async def test_a_server_error_becomes_unavailable(self) -> None:
        provider, _ = build_provider(
            FakeClient(error=_api_error(genai_errors.ServerError, 503))
        )
        with pytest.raises(ModelUnavailableError):
            await provider.embed(["a"])

    async def test_transport_failure_becomes_unavailable(self) -> None:
        provider, _ = build_provider(FakeClient(error=ConnectionError("down")))
        with pytest.raises(ModelUnavailableError):
            await provider.embed(["a"])

    async def test_the_provider_message_never_reaches_the_caller(self) -> None:
        """SECURITY: provider error bodies can quote the request -- here, the
        user's document text -- back to us."""
        for error in (
            _api_error(genai_errors.ClientError, 400),
            _api_error(genai_errors.ServerError, 500),
        ):
            provider, _ = build_provider(FakeClient(error=error))
            with pytest.raises(ModelProviderError) as caught:
                await provider.embed(["a"])
            assert "provider said something" not in str(caught.value)


# --- selection and configuration ---------------------------------------------


class TestEmbeddingSelection:
    def test_hashing_is_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.ai.gateway.get_settings", lambda: build_settings())
        provider = get_embedding_provider()
        assert isinstance(provider, HashingEmbeddingProvider)
        assert provider.dimensions == EMBEDDING_DIMENSIONS

    def test_gemini_is_built_at_the_column_width(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.ai.gateway.get_settings",
            lambda: build_settings(
                embedding_provider="gemini", gemini_api_key="not-a-real-key"
            ),
        )
        provider = get_embedding_provider()
        assert isinstance(provider, GeminiEmbeddingProvider)
        assert provider.dimensions == EMBEDDING_DIMENSIONS
        assert provider.model == DEFAULT_GEMINI_EMBEDDING_MODEL

    def test_the_model_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.ai.gateway.get_settings",
            lambda: build_settings(
                embedding_provider="gemini",
                gemini_api_key="not-a-real-key",
                embedding_model="some-other-embedding-model",
            ),
        )
        assert get_embedding_provider().model == "some-other-embedding-model"


class TestEmbeddingConfig:
    def test_gemini_embeddings_need_a_key(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            build_settings(embedding_provider="gemini")
        assert "GEMINI_API_KEY" in str(exc_info.value)

    def test_hashing_is_refused_in_production(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            build_settings(
                app_env="production",
                session_cookie_secure=True,
                ai_provider="anthropic",
                anthropic_api_key="sk-not-a-real-key",
            )
        assert "EMBEDDING_PROVIDER" in str(exc_info.value)

    def test_gemini_is_accepted_in_production(self) -> None:
        settings = build_settings(
            app_env="production",
            session_cookie_secure=True,
            ai_provider="anthropic",
            anthropic_api_key="sk-not-a-real-key",
            embedding_provider="gemini",
            gemini_api_key="not-a-real-key",
        )
        assert settings.embedding_provider == "gemini"

    def test_the_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            build_settings(embedding_timeout_seconds=0)
