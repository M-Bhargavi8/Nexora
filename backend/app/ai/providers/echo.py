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

from app.ai.base import (
    ChatMessage,
    ModelProvider,
    ModelProviderError,
    StreamEvent,
    TextDelta,
    Usage,
)

# Chunked the way a real stream arrives: several words at a time, mid-sentence
# boundaries included, so the UI is exercised against realistic fragments
# rather than one tidy paragraph.
#
# The reply contains markdown on purpose. This provider exists partly so the
# chat UI can be built without spending money, and a canned reply of plain
# prose cannot exercise the renderer at all -- the hardest rendering path
# would simply never run. The code fence is deliberately split across several
# chunks, so the renderer is also tested against *partial* markdown, which is
# what is on screen for the whole time a real reply is streaming.
_REPLY_CHUNKS: tuple[str, ...] = (
    "This is the **offline echo provider**, ",
    "so no model was called and nothing was billed.\n\n",
    "It streams a fixed reply in chunks, ",
    "so the streaming path can be tested end to end:\n\n",
    "- streamed in fragments\n",
    "- rendered as each fragment arrives\n\n",
    "| field | value |\n",
    "| --- | --- |\n",
    "| provider | echo |\n\n",
    "```python\n",
    "def greet(name: str) -> str:\n",
    '    return f"Hello, {name}"\n',
    "```\n\n",
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
    ) -> AsyncIterator[StreamEvent]:
        if not messages:
            raise ModelProviderError("Cannot stream a reply with no messages")

        for chunk in _REPLY_CHUNKS:
            if self._chunk_delay_seconds:
                await asyncio.sleep(self._chunk_delay_seconds)
            yield TextDelta(chunk)

        # Echo the most recent user turn so a test can assert the provider
        # actually received the conversation, not just that *something*
        # streamed back.
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        yield TextDelta(repr(last_user))

        # Token counts are genuinely unknown here, and are reported as such.
        # `model` is still worth recording: it makes every message this
        # provider produced identifiable in the database, so canned replies can
        # never be mistaken later for something a real model said.
        yield Usage(model=self.name)
