"""A deterministic, offline provider.

Not a toy. It exists for three concrete reasons:

1. **Tests must not call a paid API.** A suite that spends money and needs
   network access is a suite people stop running. Every test that exercises
   the chat path uses this provider or a fake SDK client.
2. **Frontend work should not cost money.** Building the chat UI means
   reloading the page hundreds of times. Each of those need not be a billed
   request.
3. **It proves the abstraction is real.** If a second provider can be dropped
   in with no change above `ModelProvider`, the boundary works. If it cannot,
   the boundary was decorative and we find out now rather than in Phase 8.

It answers with a fixed, obviously-fake reply. It must never be mistaken for a
real model, so it does not attempt to be clever -- and `Settings` refuses to
start with this provider selected in production.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from app.ai.base import ChatMessage, ModelProvider, ModelProviderError

# Chunked the way a real stream arrives: several words at a time, mid-sentence
# boundaries included, so the UI is exercised against realistic fragments
# rather than one tidy paragraph.
_REPLY_CHUNKS: tuple[str, ...] = (
    "This is the offline echo provider, ",
    "so no model was called and nothing was billed. ",
    "It streams a fixed reply in chunks ",
    "so the streaming path can be tested end to end. ",
    "You said: ",
)


class EchoProvider(ModelProvider):
    """Streams a canned reply, then echoes the user's last message."""

    name = "echo"

    def __init__(self, *, chunk_delay_seconds: float = 0.0) -> None:
        # Zero in tests so they stay fast; set it in dev to watch tokens
        # actually arrive one after another rather than in a single burst.
        self._chunk_delay_seconds = chunk_delay_seconds

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        if not messages:
            raise ModelProviderError("Cannot stream a reply with no messages")

        for chunk in _REPLY_CHUNKS:
            if self._chunk_delay_seconds:
                await asyncio.sleep(self._chunk_delay_seconds)
            yield chunk

        # Echo the most recent user turn so a test can assert the provider
        # actually received the conversation, not just that *something*
        # streamed back.
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        yield repr(last_user)
