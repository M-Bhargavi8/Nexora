"""The provider-independent model interface.

This is the most important boundary in NEXORA. Everything above it -- route
handlers, services, eventually agents -- talks to *this* interface. Nothing
above it imports a vendor SDK. When a provider changes its pricing, deprecates
a model, or is replaced by something better, we write one new class down here
and nothing else changes.

**Why the interface is split.** `ARCHITECTURE.md` §3 sketches `generate`,
`stream`, `embed`, `vision`, `tool_call` on one class. Phase 6 shows why that
would be wrong: embedding and chat generation are bought separately in the real
world. Anthropic sells no embedding model at all, so a single interface would
force the chat adapter to stub out `embed` and every embedding adapter to stub
out `stream`.

So `EmbeddingProvider` is its own interface, selected by its own setting. A
deployment can run Anthropic for chat and something else entirely for
embeddings, which is the normal arrangement rather than an exotic one.
`vision` and `tool_call` still arrive with the phases that need them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal

#: What a piece of text is being embedded *for*. Some models embed a question
#: and a passage differently, because a question and its answer rarely look
#: alike, and asking the model which side of the search a text is on measurably
#: improves retrieval. Providers without the distinction ignore it.
EmbeddingPurpose = Literal["document", "query"]

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


class EmbeddingProvider(ABC):
    """Turns text into vectors that can be compared for similarity.

    Separate from `ModelProvider` on purpose -- see the module docstring.

    **What an embedding is**, since this is the first place NEXORA needs one: a
    fixed-length list of numbers standing for a piece of text, arranged so that
    texts with similar meaning end up close together in that space. "How do I
    reset my password" and "I forgot my login" share almost no words, but a
    good embedding model places them near each other. That is the entire reason
    retrieval can find a relevant passage that does not contain the words the
    question was asked with.
    """

    #: Short identifier used in logs.
    name: str

    #: The specific model, or algorithm version, behind this provider. Two
    #: versions of one vendor's model are as incomparable as two vendors.
    model: str

    #: Length of the vectors produced. Fixed per model, and baked into the
    #: database column -- changing it means re-embedding every stored chunk,
    #: because vectors from two different models are not comparable.
    dimensions: int

    @property
    def identity(self) -> str:
        """The label stored beside every vector this provider writes.

        Vectors are only comparable when this matches exactly. Retrieval
        filters on it, so switching models makes old vectors *unsearchable*
        until they are re-embedded -- never silently compared against new ones,
        which would return confident nonsense instead of an error.
        """
        return f"{self.name}:{self.model}:{self.dimensions}"

    @abstractmethod
    async def embed(
        self,
        texts: Sequence[str],
        *,
        purpose: EmbeddingPurpose = "document",
    ) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input, in order.

        Batched rather than one-at-a-time because hosted embedding APIs charge
        and rate-limit per *request*: embedding 200 chunks individually is 200
        round trips for work that fits in a handful.

        Implementations must return vectors of exactly `dimensions` length, and
        must not reorder or drop inputs -- the caller pairs the results with
        its chunks positionally.

        Raises a `ModelProviderError` subclass on failure.
        """
        raise NotImplementedError
