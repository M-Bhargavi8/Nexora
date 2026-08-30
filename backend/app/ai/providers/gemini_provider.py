"""Google Gemini adapter.

The only module in NEXORA that imports the `google-genai` SDK. Everything else
depends on `app.ai.base.ModelProvider`, so adding this provider touched this
directory, one branch in the gateway, and two settings -- nothing above the
boundary changed, which is the whole point of having one.

Named `gemini_provider.py` rather than `gemini.py` for the same reason the
Anthropic adapter is `anthropic_provider.py`: a module that shadows the package
it imports produces a baffling ImportError.

**Which Google API this uses, and why.** Google now offers two:

* the **Interactions API**, which keeps conversation history server-side and is
  continued by passing `previous_interaction_id`;
* **`generate_content_stream`**, which is stateless -- every request carries the
  full conversation.

This adapter uses the stateless one. NEXORA owns its conversation store: it
reloads history from PostgreSQL on every turn, and *rewinds* it when a message
is edited or a reply regenerated. Server-managed history would fight that, and
editing would break outright -- there is no way to tell the server "forget the
last three turns". Google labels this API "legacy", which is a real risk worth
naming; when it matters, it is this one file that changes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.ai.base import (
    ChatMessage,
    ModelAuthError,
    ModelProvider,
    ModelProviderError,
    ModelRateLimitError,
    ModelUnavailableError,
    StreamEvent,
    TextDelta,
    Usage,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

#: NEXORA calls the assistant's turns "assistant"; Gemini calls them "model".
#: Translating here, rather than anywhere above, is exactly what the adapter
#: boundary is for -- `ChatMessage.role` stays provider-neutral.
_ROLE_TO_GEMINI = {"user": "user", "assistant": "model"}


class GeminiProvider(ModelProvider):
    """Streams completions from the Gemini Developer API."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        client: Any | None = None,
    ) -> None:
        # `client` is injectable so tests can pass a fake and never make a real
        # network call. Production always leaves it None.
        self._client = client or genai.Client(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def _to_contents(
        self, messages: Sequence[ChatMessage]
    ) -> list[genai_types.Content]:
        """Convert NEXORA's history into Gemini's wire format."""
        return [
            genai_types.Content(
                role=_ROLE_TO_GEMINI[message.role],
                parts=[genai_types.Part(text=message.content)],
            )
            for message in messages
        ]

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream Gemini's reply, then report what it cost."""
        if not messages:
            # The API rejects an empty conversation, but with a less obvious
            # message than this one.
            raise ModelProviderError("Cannot stream a reply with no messages")

        config = genai_types.GenerateContentConfig(
            max_output_tokens=self._max_tokens,
            # A separate field, not a message with a "system" role. That
            # matches NEXORA's own decision to keep the system prompt out of
            # the message list -- conflating them is the shape of a prompt
            # injection, and it is also what carries the retrieval rules in
            # app/ai/context.py.
            #
            # Omitted entirely when unset: passing None is not the same as not
            # passing it.
            **({"system_instruction": system} if system is not None else {}),
        )

        usage: Usage | None = None

        try:
            # NOTE the `await`. `generate_content_stream` is a *coroutine*
            # that returns an async iterator -- not an async generator
            # function. Writing `async for chunk in client.aio...(...)`
            # without awaiting first raises a TypeError at runtime, and it is
            # the single easiest thing to get wrong here. Verified against the
            # installed SDK rather than assumed.
            stream = await self._client.aio.models.generate_content_stream(
                model=self._model,
                contents=self._to_contents(messages),
                config=config,
            )

            async for chunk in stream:
                text = getattr(chunk, "text", None)
                if text:
                    yield TextDelta(text)

                # Usage arrives on chunks as the reply progresses; the last one
                # to carry it holds the final counts. Kept rather than yielded
                # immediately so exactly one Usage event is emitted, after the
                # text -- the contract every other provider follows.
                metadata = getattr(chunk, "usage_metadata", None)
                if metadata is not None:
                    usage = Usage(
                        model=getattr(chunk, "model_version", None) or self._model,
                        input_tokens=getattr(metadata, "prompt_token_count", None),
                        # `candidates_token_count` is the reply itself.
                        # `total_token_count` would double-count the prompt,
                        # and `thoughts_token_count` is billed separately on
                        # thinking models -- neither belongs in this column.
                        output_tokens=getattr(metadata, "candidates_token_count", None),
                    )

        except genai_errors.ClientError as exc:
            # 4xx. The SDK collapses every client-side failure into one class,
            # so the HTTP status is what distinguishes them -- unlike the
            # Anthropic SDK, which raises a distinct type per condition.
            raise self._client_error(exc) from exc
        except genai_errors.ServerError as exc:
            # SECURITY: log the status server-side, return something generic.
            # `str(exc)` embeds the provider's raw response body, which can
            # echo request content straight back to the user.
            logger.warning("Model provider returned server error %s", exc.code)
            raise ModelUnavailableError("Model provider returned an error") from exc
        except genai_errors.APIError as exc:
            logger.warning("Model provider API error %s", getattr(exc, "code", "?"))
            raise ModelUnavailableError("Model provider returned an error") from exc
        except (ConnectionError, TimeoutError, OSError) as exc:
            # The SDK surfaces transport failures as ordinary network errors
            # rather than wrapping them.
            logger.warning("Could not reach the model provider: %s", type(exc).__name__)
            raise ModelUnavailableError("Model provider is unreachable") from exc

        if usage is not None:
            yield usage

    @staticmethod
    def _client_error(exc: genai_errors.ClientError) -> ModelProviderError:
        """Map a 4xx onto the right NEXORA error.

        Never includes the provider's message. Those bodies quote the request
        back, and this text reaches the browser.
        """
        code = getattr(exc, "code", None)

        if code in (401, 403):
            # An operator problem -- a missing, invalid or unauthorised key --
            # never the end user's fault, so the message must not suggest they
            # did something wrong.
            logger.error("Model provider rejected our credentials (HTTP %s)", code)
            return ModelAuthError("Model provider authentication failed")

        if code == 429:
            logger.warning("Model provider rate limited us")
            return ModelRateLimitError("Model provider is rate limiting requests")

        if code == 404:
            # Almost always a model id the project cannot use: wrong name, or
            # not available on this tier. Worth its own log line, because the
            # generic message below sends people looking in the wrong place.
            logger.error(
                "Model provider does not recognise the configured model -- "
                "check AI_MODEL is available to this API key"
            )
            return ModelUnavailableError("The configured model is unavailable")

        logger.warning("Model provider rejected the request (HTTP %s)", code)
        return ModelUnavailableError("Model provider returned an error")
