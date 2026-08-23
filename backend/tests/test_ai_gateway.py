"""Tests for the AI gateway and the offline echo provider.

Nothing here touches a network or spends money. That is a requirement, not a
convenience: a test suite that makes billed calls is one people stop running.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest

from app.ai.base import (
    ChatMessage,
    ModelProvider,
    ModelProviderError,
    StreamEvent,
    TextDelta,
    Usage,
)
from app.ai.gateway import get_provider
from app.ai.providers.echo import EchoProvider
from app.core.config import Settings

VALID_SECRET = "a" * 32
VALID_DB_URL = "postgresql+asyncpg://user:pass@localhost:5432/nexora"


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "secret_key": VALID_SECRET,
        "database_url": VALID_DB_URL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def clear_provider_cache() -> Iterator[None]:
    """The gateway caches its provider, so tests must not inherit each other's."""
    get_provider.cache_clear()
    yield
    get_provider.cache_clear()


async def collect(
    provider: ModelProvider, messages: Sequence[ChatMessage]
) -> list[StreamEvent]:
    return [event async for event in provider.stream(messages)]


async def collect_text(
    provider: ModelProvider, messages: Sequence[ChatMessage]
) -> str:
    return "".join(
        event.text for event in await collect(provider, messages)
        if isinstance(event, TextDelta)
    )


class TestEchoProvider:
    async def test_streams_more_than_one_chunk(self) -> None:
        """If it returned everything at once, the streaming path downstream
        would never actually be exercised."""
        events = await collect(EchoProvider(), [ChatMessage("user", "hello")])

        assert sum(isinstance(e, TextDelta) for e in events) > 1

    async def test_echoes_the_last_user_message(self) -> None:
        """Proves the provider really received the conversation, rather than
        just emitting canned text regardless of input."""
        text = await collect_text(EchoProvider(), [ChatMessage("user", "marmalade")])

        assert "marmalade" in text

    async def test_echoes_the_most_recent_user_turn_not_the_first(self) -> None:
        conversation = [
            ChatMessage("user", "first question"),
            ChatMessage("assistant", "an answer"),
            ChatMessage("user", "second question"),
        ]

        text = await collect_text(EchoProvider(), conversation)

        assert "second question" in text
        assert "first question" not in text

    async def test_rejects_an_empty_conversation(self) -> None:
        with pytest.raises(ModelProviderError):
            await collect(EchoProvider(), [])

    async def test_reports_which_provider_answered(self) -> None:
        """Canned replies must stay identifiable, so they can never later be
        mistaken for something a real model said."""
        events = await collect(EchoProvider(), [ChatMessage("user", "hi")])

        usage = [e for e in events if isinstance(e, Usage)]
        assert len(usage) == 1
        assert usage[0].model == "echo"
        # Genuinely unknown here. Inventing plausible numbers would put
        # fiction in a cost column.
        assert usage[0].input_tokens is None

    async def test_says_plainly_that_it_is_not_a_real_model(self) -> None:
        """SECURITY-adjacent: canned text must never be mistaken for a model
        answer by whoever is reading the screen."""
        text = await collect_text(EchoProvider(), [ChatMessage("user", "hi")])

        assert "offline echo provider" in text.lower()


class TestGatewaySelection:
    def test_returns_the_echo_provider_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.ai.gateway.get_settings", lambda: build_settings(ai_provider="echo")
        )

        assert isinstance(get_provider(), EchoProvider)

    def test_returns_the_anthropic_provider_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Importing here keeps this test file runnable even if the SDK were
        # ever absent, which is the point of the gateway's lazy import.
        from app.ai.providers.anthropic_provider import AnthropicProvider

        monkeypatch.setattr(
            "app.ai.gateway.get_settings",
            lambda: build_settings(
                ai_provider="anthropic", anthropic_api_key="sk-not-a-real-key"
            ),
        )

        provider = get_provider()

        assert isinstance(provider, AnthropicProvider)
        assert provider.name == "anthropic"

    def test_builds_the_provider_only_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each provider owns an HTTP connection pool. Building one per
        request would leak sockets and lose connection reuse."""
        monkeypatch.setattr(
            "app.ai.gateway.get_settings", lambda: build_settings(ai_provider="echo")
        )

        assert get_provider() is get_provider()
