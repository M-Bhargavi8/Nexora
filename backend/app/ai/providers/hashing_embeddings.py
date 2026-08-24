"""A deterministic, offline embedding provider.

**Read this before trusting anything it retrieves.**

This is a *lexical* embedding, not a semantic one. It hashes words into buckets
and counts them, so two passages score as similar when they share vocabulary.
It has no idea that "car" and "automobile" mean the same thing, and it will not
connect "I forgot my login" to a passage about resetting a password. A real
embedding model would; that is precisely what the model is for.

So why ship it?

1. **Tests must not call a paid API.** Every retrieval test in this phase runs
   against this provider, deterministically, offline, for free.
2. **The pipeline is what Phase 6 is proving.** Chunking, storing vectors in
   pgvector, searching by cosine distance, scoping results to one user,
   injecting passages into a prompt and citing them -- none of that changes
   when the embedding model does. Getting it working end to end with an honest
   stand-in is worth more than getting it half-working with a real one.
3. **It keeps the abstraction real.** If a hosted provider cannot be dropped in
   without touching anything above `EmbeddingProvider`, the boundary was
   decorative, and this is where that shows up.

`Settings` refuses to start with this provider selected in production, for the
same reason it refuses the echo chat provider: retrieval quality this weak must
never be mistaken for the real thing.

The technique is the "hashing trick": rather than building a vocabulary of
every word ever seen and assigning each an index, hash the word and take the
remainder. It needs no fitted vocabulary and no state, which is what makes the
output stable across processes and across restarts -- a stored vector must stay
comparable to one computed tomorrow.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Sequence

from app.ai.base import EmbeddingProvider

# Small enough to keep rows cheap, large enough that unrelated words rarely
# collide into the same bucket. Real models are 768-3072; the number here is
# not a claim about quality, only about storage.
DIMENSIONS = 512

# Words, numbers, and intra-word apostrophes/hyphens. Deliberately simple:
# a heavier tokenizer would be a dependency and would not make a lexical
# embedding meaningfully better.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")

# The most common English words carry almost no retrieval signal -- every
# document contains them, so they push every vector towards the same direction
# and make everything look mildly similar to everything.
_STOPWORDS = frozenset(
    """
    a an and are as at be been but by for from has have he her his i in is it
    its of on or she that the their them there they this to was were what
    when where which who will with would you your
    """.split()
)


def _tokenize(text: str) -> list[str]:
    return [
        token
        for token in _TOKEN_RE.findall(text.lower())
        if token not in _STOPWORDS
    ]


def _bucket(token: str, dimensions: int) -> int:
    """Map a token to a vector index, stably across processes.

    `hash()` is deliberately *not* used: Python randomises string hashing per
    process (PYTHONHASHSEED), so vectors written today would not match vectors
    computed after a restart. A stored embedding that silently stops being
    comparable is the worst kind of bug -- retrieval simply gets quietly worse.
    """
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dimensions


class HashingEmbeddingProvider(EmbeddingProvider):
    """Offline lexical embeddings. See the module docstring for the caveats."""

    name = "hashing"

    def __init__(self, dimensions: int = DIMENSIONS) -> None:
        self.dimensions = dimensions

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _tokenize(text)

        if not tokens:
            # An all-stopword or empty chunk. Returning zeros is correct and
            # important: cosine similarity against a zero vector is undefined,
            # and pgvector reports it as no similarity rather than perfect
            # similarity -- so an empty chunk cannot match everything.
            return vector

        counts = Counter(tokens)
        for token, count in counts.items():
            # Sub-linear term frequency. A word repeated forty times is more
            # relevant than one used once, but not forty times more -- without
            # damping, one repeated word drowns out the rest of the passage.
            vector[_bucket(token, self.dimensions)] += 1.0 + math.log(count)

        # L2 normalisation makes cosine similarity a plain dot product, and --
        # the part that matters -- stops long chunks from outranking short ones
        # merely for containing more words.
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch. Pure computation, so there is nothing to await."""
        return [self._embed_one(text) for text in texts]
