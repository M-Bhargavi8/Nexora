"""Tests for the Gemini adapter.

Every test drives a **fake SDK client**. No network, no API key, no cost. What
is under test is our adapter -- the request we build, the roles we translate,
and the errors we map -- not Google's service.

Whether a given model id actually exists on a given project is a question only
the live API can answer, and is verified separately; nothing here pretends to.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from google.genai import errors as genai_errors

from app.ai.base import (
    ChatMessage,
    ModelAuthError,
    ModelProviderError,
    ModelRateLimitError,
    ModelUnavailableError,
    TextDelta,
    Usage,
)
from app.ai.providers.gemini_provider import GeminiProvider

MESSAGES = [ChatMessage("user", "hello")]


# --- a fake standing in for genai.Client ------------------------------------


class _FakeUsageMetadata:
    def __init__(
        self,
        prompt_token_count: int | None = 11,
        candidates_token_count: int | None = 7,
    ) -> None:
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count
        # Present on real responses and deliberately NOT used for
        # output_tokens: it double-counts the prompt.
        self.total_token_count = (prompt_token_count or 0) + (
            candidates_token_count or 0
        )
        self.thoughts_token_count = 3


class _FakeChunk:
    def __init__(
        self,
        text: str | None = None,
        usage: _FakeUsageMetadata | None = None,
        model_version: str | None = None,
    ) -> None:
        self.text = text
        self.usage_metadata = usage
        self.model_version = model_version


class _FakeModels:
    def __init__(
        self,
        chunks: Sequence[_FakeChunk],
        error: Exception | None,
        record: dict[str, Any],
    ) -> None:
        self._chunks = chunks
        self._error = error
        self._record = record

    async def generate_content_stream(self, **kwargs: Any) -> AsyncIterator[Any]:
        """A coroutine returning an async iterator.

        Shaped exactly like the real SDK method -- which is a coroutine, not an
        async generator. Getting that wrong is the easiest mistake in this
        adapter, so the fake reproduces it rather than smoothing it over.
        """
        self._record.update(kwargs)
        if self._error is not None:
            raise self._error

        async def _iterate() -> AsyncIterator[Any]:
            for chunk in self._chunks:
                yield chunk

        return _iterate()


class _FakeAio:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models


class FakeClient:
    def __init__(
        self,
        chunks: Sequence[_FakeChunk] = (),
        error: Exception | None = None,
    ) -> None:
        self.record: dict[str, Any] = {}
        self.aio = _FakeAio(_FakeModels(chunks, error, self.record))


def build_provider(
    chunks: Sequence[_FakeChunk] = (),
    error: Exception | None = None,
    *,
    model: str = "gemini-3.7-flash",
    max_tokens: int = 4096,
) -> tuple[GeminiProvider, FakeClient]:
    client = FakeClient(chunks, error)
    provider = GeminiProvider(
        api_key="not-a-real-key",
        model=model,
        max_tokens=max_tokens,
        client=client,
    )
    return provider, client


async def collect(provider: GeminiProvider, **kwargs: Any) -> list[Any]:
    return [event async for event in provider.stream(MESSAGES, **kwargs)]


async def collect_text(provider: GeminiProvider, **kwargs: Any) -> list[str]:
    return [
        event.text
        for event in await collect(provider, **kwargs)
        if isinstance(event, TextDelta)
    ]


def _api_error(cls: type, code: int) -> Exception:
    """Build a real SDK error with a given HTTP status."""
    return cls(code, {"error": {"message": "provider said something", "status": "X"}})


# --- streaming --------------------------------------------------------------


class TestStreaming:
    async def test_yields_each_chunk_in_order(self) -> None:
        provider, _ = build_provider(
            [_FakeChunk("Hello "), _FakeChunk("world"), _FakeChunk("!")]
        )

        assert await collect_text(provider) == ["Hello ", "world", "!"]

    async def test_rejects_an_empty_conversation(self) -> None:
        provider, _ = build_provider()

        with pytest.raises(ModelProviderError):
            [event async for event in provider.stream([])]

    async def test_skips_chunks_with_no_text(self) -> None:
        """Real streams carry chunks that hold only metadata. Yielding an empty
        TextDelta for each would put empty SSE events on the wire."""
        provider, _ = build_provider(
            [_FakeChunk("Hello"), _FakeChunk(None), _FakeChunk(""), _FakeChunk("!")]
        )

        assert await collect_text(provider) == ["Hello", "!"]

    async def test_reports_token_usage_after_the_reply(self) -> None:
        provider, _ = build_provider(
            [_FakeChunk("Hi"), _FakeChunk(None, _FakeUsageMetadata(11, 7))]
        )

        events = await collect(provider)

        usage = [event for event in events if isinstance(event, Usage)]
        assert len(usage) == 1
        assert usage[0].input_tokens == 11
        assert usage[0].output_tokens == 7

    async def test_usage_arrives_after_every_text_chunk(self) -> None:
        """The contract every provider follows: text first, then at most one
        Usage. Persistence relies on it."""
        provider, _ = build_provider(
            [
                _FakeChunk("one"),
                _FakeChunk("two", _FakeUsageMetadata()),
                _FakeChunk("three"),
            ]
        )

        events = await collect(provider)

        assert isinstance(events[-1], Usage)
        assert sum(isinstance(e, Usage) for e in events) == 1

    async def test_output_tokens_exclude_the_prompt(self) -> None:
        """`total_token_count` would double-count the prompt, and thinking
        tokens are billed separately -- neither belongs in this column."""
        provider, _ = build_provider(
            [_FakeChunk("x", _FakeUsageMetadata(prompt_token_count=100,
                                                candidates_token_count=5))]
        )

        usage = [e for e in await collect(provider) if isinstance(e, Usage)][0]

        assert usage.output_tokens == 5

    async def test_reports_the_model_that_answered(self) -> None:
        provider, _ = build_provider(
            [_FakeChunk("x", _FakeUsageMetadata(), model_version="gemini-3.7-flash-001")]
        )

        usage = [e for e in await collect(provider) if isinstance(e, Usage)][0]

        assert usage.model == "gemini-3.7-flash-001"

    async def test_falls_back_to_the_configured_model_name(self) -> None:
        provider, _ = build_provider([_FakeChunk("x", _FakeUsageMetadata())])

        usage = [e for e in await collect(provider) if isinstance(e, Usage)][0]

        assert usage.model == "gemini-3.7-flash"

    async def test_a_stream_with_no_usage_yields_no_usage_event(self) -> None:
        """Inventing plausible token counts would put fiction in a cost
        column."""
        provider, _ = build_provider([_FakeChunk("just text")])

        events = await collect(provider)

        assert not any(isinstance(event, Usage) for event in events)


# --- request shape ----------------------------------------------------------


class TestRequestShape:
    async def test_sends_the_configured_model_and_token_cap(self) -> None:
        provider, client = build_provider(
            [_FakeChunk("x")], model="gemini-3.5-flash", max_tokens=256
        )

        await collect(provider)

        assert client.record["model"] == "gemini-3.5-flash"
        assert client.record["config"].max_output_tokens == 256

    async def test_translates_the_assistant_role_to_model(self) -> None:
        """NEXORA says "assistant"; Gemini says "model". Sending the wrong one
        is rejected by the API."""
        provider, client = build_provider([_FakeChunk("x")])

        history = [
            ChatMessage("user", "first"),
            ChatMessage("assistant", "reply"),
            ChatMessage("user", "second"),
        ]
        [event async for event in provider.stream(history)]

        roles = [content.role for content in client.record["contents"]]
        assert roles == ["user", "model", "user"]

    async def test_sends_the_message_text(self) -> None:
        provider, client = build_provider([_FakeChunk("x")])

        [event async for event in provider.stream([ChatMessage("user", "hello there")])]

        contents = client.record["contents"]
        assert contents[0].parts[0].text == "hello there"

    async def test_omits_the_system_instruction_entirely_when_not_given(self) -> None:
        """Passing None is not the same as not passing it."""
        provider, client = build_provider([_FakeChunk("x")])

        await collect(provider)

        assert client.record["config"].system_instruction is None

    async def test_includes_the_system_instruction_when_given(self) -> None:
        provider, client = build_provider([_FakeChunk("x")])

        await collect(provider, system="You are NEXORA.")

        assert client.record["config"].system_instruction == "You are NEXORA."

    async def test_the_system_prompt_never_becomes_a_message(self) -> None:
        """SECURITY: the system prompt stays a separate field. Folding it into
        the message list is the shape of a prompt injection, and it is what
        carries the retrieval rules."""
        provider, client = build_provider([_FakeChunk("x")])

        await collect(provider, system="SECRET RULES")

        for content in client.record["contents"]:
            for part in content.parts:
                assert "SECRET RULES" not in (part.text or "")


# --- error mapping ----------------------------------------------------------


class TestErrorMapping:
    @pytest.mark.parametrize("code", [401, 403])
    async def test_credentials_become_an_auth_error(self, code: int) -> None:
        provider, _ = build_provider(error=_api_error(genai_errors.ClientError, code))

        with pytest.raises(ModelAuthError):
            await collect(provider)

    async def test_429_becomes_a_rate_limit_error(self) -> None:
        provider, _ = build_provider(
            error=_api_error(genai_errors.ClientError, 429)
        )

        with pytest.raises(ModelRateLimitError):
            await collect(provider)

    async def test_404_becomes_unavailable(self) -> None:
        """The usual cause is a model id this project cannot use."""
        provider, _ = build_provider(
            error=_api_error(genai_errors.ClientError, 404)
        )

        with pytest.raises(ModelUnavailableError):
            await collect(provider)

    async def test_a_server_error_becomes_unavailable(self) -> None:
        provider, _ = build_provider(
            error=_api_error(genai_errors.ServerError, 503)
        )

        with pytest.raises(ModelUnavailableError):
            await collect(provider)

    @pytest.mark.parametrize(
        "exc", [ConnectionError("down"), TimeoutError("slow"), OSError("no route")]
    )
    async def test_transport_failures_become_unavailable(
        self, exc: Exception
    ) -> None:
        provider, _ = build_provider(error=exc)

        with pytest.raises(ModelUnavailableError):
            await collect(provider)

    async def test_every_failure_is_a_nexora_error(self) -> None:
        """Callers catch ModelProviderError, never a vendor type. If a Google
        exception escaped, swapping providers would mean editing error
        handling everywhere."""
        for error in (
            _api_error(genai_errors.ClientError, 400),
            _api_error(genai_errors.ServerError, 500),
            ConnectionError("down"),
        ):
            provider, _ = build_provider(error=error)
            with pytest.raises(ModelProviderError):
                await collect(provider)

    async def test_the_provider_message_never_reaches_the_caller(self) -> None:
        """SECURITY: provider error bodies quote the request back, and this
        text reaches the browser."""
        provider, _ = build_provider(
            error=_api_error(genai_errors.ClientError, 400)
        )

        with pytest.raises(ModelProviderError) as caught:
            await collect(provider)

        assert "provider said something" not in str(caught.value)
