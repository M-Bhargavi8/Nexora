"""Anthropic (Claude) adapter.

The only module in NEXORA that imports the `anthropic` SDK. Everything else
depends on `app.ai.base.ModelProvider`, so replacing or adding a provider
touches this directory and nothing else.

Named `anthropic_provider.py`, not `anthropic.py`, on purpose: a module named
`anthropic` inside a package that also imports the real `anthropic` package is
a classic shadowing bug that produces a baffling ImportError.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from app.ai.base import (
    ChatMessage,
    ModelAuthError,
    ModelProvider,
    ModelProviderError,
    ModelRateLimitError,
    ModelUnavailableError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


class AnthropicProvider(ModelProvider):
    """Streams completions from the Claude API."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        client: AsyncAnthropic | None = None,
    ) -> None:
        # `client` is injectable so tests can pass a fake and never make a
        # paid network call. Production always leaves it None.
        self._client = client or AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream Claude's reply as text chunks."""
        if not messages:
            # The API rejects an empty conversation, but with a less obvious
            # message than this one.
            raise ModelProviderError("Cannot stream a reply with no messages")

        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }

        # Omitted entirely when unset. Passing system=None is not the same as
        # not passing it, and the API rejects the former.
        if system is not None:
            request["system"] = system

        # NOT PASSED, deliberately: temperature, top_p, top_k. Those sampling
        # parameters were removed on current Claude models and now return a
        # 400. Anyone "helpfully" adding temperature=0.7 here breaks every
        # request -- hence this note rather than silence.
        #
        # Also not passed: `thinking`. On the models we target, omitting it
        # runs adaptive thinking, which is what we want for chat.

        try:
            async with self._client.messages.stream(**request) as stream:
                async for chunk in stream.text_stream:
                    yield chunk
        except anthropic.AuthenticationError as exc:
            # Never let the key or the provider's wording reach the caller.
            logger.error("Model provider rejected our credentials")
            raise ModelAuthError("Model provider authentication failed") from exc
        except anthropic.PermissionDeniedError as exc:
            logger.error("Model provider denied permission for this request")
            raise ModelAuthError("Model provider denied permission") from exc
        except anthropic.RateLimitError as exc:
            logger.warning("Model provider rate limited us")
            raise ModelRateLimitError("Model provider is rate limiting requests") from exc
        except anthropic.APIConnectionError as exc:
            logger.warning("Could not reach the model provider: %s", exc)
            raise ModelUnavailableError("Model provider is unreachable") from exc
        except anthropic.APIStatusError as exc:
            # SECURITY: log the status server-side, return something generic.
            # Provider error bodies can echo request content back.
            logger.warning("Model provider returned HTTP %s", exc.status_code)
            raise ModelUnavailableError("Model provider returned an error") from exc
