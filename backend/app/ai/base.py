"""The provider-independent model interface.

This is the most important boundary in NEXORA. Everything above it -- route
handlers, services, eventually agents -- talks to *this* interface. Nothing
above it imports a vendor SDK. When a provider changes its pricing, deprecates
a model, or is replaced by something better, we write one new class down here
and nothing else changes.

**Why only `stream()` for now.** `ARCHITECTURE.md` §3 sketches a fuller
interface: `generate`, `stream`, `embed`, `vision`, `tool_call`. Declaring all
five today would mean four abstract methods that every provider must stub out
and no caller ever invokes -- dead weight that still has to be maintained.
They arrive with the phase that needs them: `embed` in Phase 6 (RAG),
`vision` in Phase 5 (images), `tool_call` in Phase 7 (tools). The interface
grows with real requirements, not with speculation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal

# Only the two roles a conversation actually contains. A system prompt is a
# separate parameter rather than a role, because that is how it is modelled by
# the providers we target -- and conflating them makes it possible for user
# text to be mistaken for system instructions, which is the shape of a prompt
# injection.
Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A piece of the reply as it is generated.

    Chunks are fragments, not tokens and not whole words. Concatenate them;
    never treat one as meaningful on its own.
    """

    text: str


@dataclass(frozen=True, slots=True)
class Usage:
    """Reported once, after the reply is complete.

    Why the stream yields this rather than only text: the message columns that
    record what a turn cost have to be filled from somewhere, and a side
    channel (a mutable dict passed in, a `last_usage()` method) would be
    stateful and racy under concurrency. An event in the stream is explicit
    and cannot be read for the wrong request.

    Token counts are optional because not every provider reports them -- the
    offline one genuinely has none, and inventing plausible numbers would put
    fiction in a cost column.
    """

    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


#: What a provider yields. A union rather than a bare string so that usage can
#: travel with the reply. Kept to exactly these two members: tool-call events
#: arrive in Phase 7, when there is something that actually emits them.
StreamEvent = TextDelta | Usage


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One turn of a conversation, in provider-neutral form.

    Frozen so a message cannot be mutated after it has been validated, and
    `slots` because a long conversation holds a lot of these.
    """

    role: Role
    content: str


class ModelProviderError(Exception):
    """Base class for every failure that comes from a model provider.

    Callers catch *this*, not a vendor's exception type. If route handlers
    caught `anthropic.RateLimitError` directly, the abstraction would leak and
    swapping providers would mean editing error handling everywhere.
    """


class ModelAuthError(ModelProviderError):
    """The credential is missing, invalid, or lacks permission.

    An operator problem, never the end user's fault -- so it must never be
    reported to the browser as though the user did something wrong.
    """


class ModelRateLimitError(ModelProviderError):
    """The provider is throttling us. Retrying later may succeed."""


class ModelUnavailableError(ModelProviderError):
    """The provider is unreachable or returned a server error."""


class ModelProvider(ABC):
    """What every model adapter must implement."""

    #: Short identifier used in logs and in the health payload.
    name: str

    @abstractmethod
    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a reply as a sequence of events.

        Yields `TextDelta` for each fragment of the reply, then at most one
        `Usage` once the reply is complete.

        Declared as a normal `def` returning an `AsyncIterator`, not as
        `async def`. Implementations are async generators, and an async
        generator function returns its iterator immediately rather than
        returning a coroutine that must be awaited first. Typing it this way
        means callers write `async for chunk in provider.stream(...)` without
        an extra `await`, and the type checker agrees with them.

        Raises a `ModelProviderError` subclass on failure.
        """
        raise NotImplementedError
