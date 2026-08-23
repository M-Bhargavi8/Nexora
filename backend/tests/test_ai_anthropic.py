"""Tests for the Anthropic adapter.

Every test drives a **fake SDK client**. No network, no API key, no cost. What
is under test is our adapter -- the request we build and the errors we map --
not Anthropic's service.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import anthropic
import httpx2
import pytest

from app.ai.base import (
    ChatMessage,
    ModelAuthError,
    ModelProviderError,
    ModelRateLimitError,
    ModelUnavailableError,
)
from app.ai.providers.anthropic_provider import AnthropicProvider

MESSAGES = [ChatMessage("user", "hello")]


# --- a fake standing in for anthropic.AsyncAnthropic ------------------------


class _FakeStream:
    def __init__(self, chunks: Sequence[str]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    @property
    def text_stream(self) -> AsyncIterator[str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str]:
        for chunk in self._chunks:
            yield chunk


class _FakeMessages:
    def __init__(self, chunks: Sequence[str], error: Exception | None) -> None:
        self._chunks = chunks
        self._error = error
        #: Every request the adapter built, so tests can assert on its shape.
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return _FakeStream(self._chunks)


class FakeClient:
    def __init__(
        self, chunks: Sequence[str] = ("Hel", "lo"), error: Exception | None = None
    ) -> None:
        self.messages = _FakeMessages(chunks, error)


def build_provider(
    chunks: Sequence[str] = ("Hel", "lo"), error: Exception | None = None
) -> tuple[AnthropicProvider, FakeClient]:
    client = FakeClient(chunks, error)
    provider = AnthropicProvider(
        api_key="sk-not-a-real-key",
        model="claude-opus-5",
        max_tokens=4096,
        client=client,  # type: ignore[arg-type]
    )
    return provider, client


async def collect(provider: AnthropicProvider, **kwargs: Any) -> list[str]:
    return [chunk async for chunk in provider.stream(MESSAGES, **kwargs)]


# --- helpers for building real SDK exceptions -------------------------------


def _response(status: int) -> httpx2.Response:
    # The SDK's error classes require a real response object. anthropic 1.x is
    # built on httpx2, so these must be httpx2 types, not httpx ones.
    return httpx2.Response(
        status,
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


def _status_error(cls: type, status: int) -> Exception:
    return cls("boom", response=_response(status), body=None)


class TestStreaming:
    async def test_yields_each_chunk_in_order(self) -> None:
        provider, _ = build_provider(chunks=("Hel", "lo ", "world"))

        assert await collect(provider) == ["Hel", "lo ", "world"]

    async def test_rejects_an_empty_conversation(self) -> None:
        provider, client = build_provider()

        with pytest.raises(ModelProviderError):
            [c async for c in provider.stream([])]

        assert client.messages.calls == [], "must not call the API at all"


class TestRequestShape:
    async def test_sends_the_configured_model_and_token_cap(self) -> None:
        provider, client = build_provider()

        await collect(provider)

        request = client.messages.calls[0]
        assert request["model"] == "claude-opus-5"
        assert request["max_tokens"] == 4096

    async def test_converts_messages_to_the_wire_format(self) -> None:
        provider, client = build_provider()
        conversation = [
            ChatMessage("user", "first"),
            ChatMessage("assistant", "second"),
        ]

        [c async for c in provider.stream(conversation)]

        assert client.messages.calls[0]["messages"] == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]

    async def test_omits_system_entirely_when_not_given(self) -> None:
        """Passing system=None is not the same as not passing it -- the API
        rejects an explicit null."""
        provider, client = build_provider()

        await collect(provider)

        assert "system" not in client.messages.calls[0]

    async def test_includes_system_when_given(self) -> None:
        provider, client = build_provider()

        await collect(provider, system="You are terse.")

        assert client.messages.calls[0]["system"] == "You are terse."

    @pytest.mark.parametrize("removed", ["temperature", "top_p", "top_k"])
    async def test_never_sends_removed_sampling_parameters(
        self, removed: str
    ) -> None:
        """SECURITY/correctness regression guard.

        These parameters were removed on current Claude models and now return
        a 400. Adding `temperature=0.7` "to make it less random" would break
        every single request, and the failure is far from the cause. This test
        is what stops that edit landing.
        """
        provider, client = build_provider()

        await collect(provider)

        assert removed not in client.messages.calls[0]


class TestErrorMapping:
    """Vendor exceptions must never escape the adapter.

    If a route handler had to catch `anthropic.RateLimitError`, the
    abstraction would be a lie and swapping providers would mean editing error
    handling across the codebase.
    """

    async def test_authentication_error_becomes_model_auth_error(self) -> None:
        provider, _ = build_provider(
            error=_status_error(anthropic.AuthenticationError, 401)
        )

        with pytest.raises(ModelAuthError):
            await collect(provider)

    async def test_permission_denied_becomes_model_auth_error(self) -> None:
        provider, _ = build_provider(
            error=_status_error(anthropic.PermissionDeniedError, 403)
        )

        with pytest.raises(ModelAuthError):
            await collect(provider)

    async def test_rate_limit_becomes_model_rate_limit_error(self) -> None:
        provider, _ = build_provider(
            error=_status_error(anthropic.RateLimitError, 429)
        )

        with pytest.raises(ModelRateLimitError):
            await collect(provider)

    async def test_connection_error_becomes_model_unavailable(self) -> None:
        provider, _ = build_provider(
            error=anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            )
        )

        with pytest.raises(ModelUnavailableError):
            await collect(provider)

    async def test_server_error_becomes_model_unavailable(self) -> None:
        provider, _ = build_provider(
            error=_status_error(anthropic.InternalServerError, 500)
        )

        with pytest.raises(ModelUnavailableError):
            await collect(provider)

    async def test_the_provider_message_is_not_leaked_to_the_caller(self) -> None:
        """Provider error bodies can echo request content back. Ours must be
        generic; the detail is logged server-side instead."""
        provider, _ = build_provider(
            error=_status_error(anthropic.AuthenticationError, 401)
        )

        with pytest.raises(ModelAuthError) as exc_info:
            await collect(provider)

        assert "boom" not in str(exc_info.value)
