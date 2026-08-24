"""Splitting a document into retrievable passages.

Pure text handling, no database and no model calls, so it can be tested
exhaustively and cheaply.

**The decision this module encodes.** Chunk size is the central trade-off in
retrieval and there is no universally correct answer:

* Chunks that are too small retrieve precisely but arrive stripped of context.
  "It rose 18 percent" is a perfect match for a question about growth and
  useless without the sentence naming what rose.
* Chunks that are too large carry their context but dilute the match -- one
  relevant sentence among thirty makes the whole passage look only faintly
  relevant -- and every retrieved chunk is tokens paid for on every question.

The approach here splits on the boundaries a document already has (blank lines,
then sentences) and only falls back to cutting mid-text when a single run has
none. Splitting on structure rather than on a character count is what keeps a
passage readable, and a readable passage is what the model can actually use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A blank line is the strongest structural signal in plain text: it separates
# paragraphs in prose, records in an extract, and rows in the flattened tables
# produced by the DOCX and XLSX extractors.
_PARAGRAPH_RE = re.compile(r"\n\s*\n")

# Sentence ends: punctuation, optional closing quote or bracket, then space.
# Deliberately not a full sentence tokenizer -- it would be a dependency, and
# the failure mode here is a slightly awkward split rather than a wrong answer.
_SENTENCE_RE = re.compile(r"(?<=[.!?])[\"')\]]*\s+")


@dataclass(frozen=True, slots=True)
class Chunk:
    """One passage, and where it came from in the source text."""

    content: str
    start_offset: int
    end_offset: int


def _split_long_run(text: str, limit: int) -> list[str]:
    """Break a run with no usable boundary into pieces of at most `limit`.

    The last resort. Reached by things that genuinely have no sentence
    structure -- a base64 blob, a minified stylesheet, a single enormous line
    of CSV -- where any split point is equally arbitrary.
    """
    return [text[index : index + limit] for index in range(0, len(text), limit)]


def _segments(text: str, limit: int) -> list[str]:
    """Break text into units no larger than `limit`, splitting on structure.

    Paragraphs first; any paragraph still too large is split into sentences;
    any sentence still too large is cut.
    """
    units: list[str] = []

    for paragraph in _PARAGRAPH_RE.split(text):
        if not paragraph.strip():
            continue
        if len(paragraph) <= limit:
            units.append(paragraph)
            continue

        for sentence in _SENTENCE_RE.split(paragraph):
            if not sentence.strip():
                continue
            if len(sentence) <= limit:
                units.append(sentence)
            else:
                units.extend(_split_long_run(sentence, limit))

    return units


def chunk_text(text: str, *, size: int, overlap: int) -> list[Chunk]:
    """Split `text` into overlapping passages of roughly `size` characters.

    `overlap` is how much of the end of one chunk is repeated at the start of
    the next. **Why repeat anything:** a fact that straddles a boundary would
    otherwise be findable from neither side -- the sentence naming the subject
    lands in one chunk and the figure in the next, so a question mentioning
    both matches neither well. Overlap costs some duplicated storage to stop
    the boundary from being a blind spot.

    Offsets are into the *original* text, so a retrieved passage can be located
    in the document it came from.
    """
    if size <= 0:
        raise ValueError("Chunk size must be positive.")
    if overlap < 0:
        raise ValueError("Chunk overlap cannot be negative.")
    if overlap >= size:
        # Each chunk would begin at or before the previous one started, so the
        # loop would never advance. Failing loudly beats hanging.
        raise ValueError("Chunk overlap must be smaller than chunk size.")

    stripped = text.strip()
    if not stripped:
        return []

    units = _segments(stripped, size)
    if not units:
        return []

    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_length = 0
    # Where to resume searching for each unit, so that a repeated sentence does
    # not report the offset of its first occurrence.
    search_from = 0
    offsets: list[tuple[int, int]] = []

    def locate(unit: str) -> tuple[int, int]:
        nonlocal search_from
        start = stripped.find(unit, search_from)
        if start == -1:  # pragma: no cover - defensive; units come from stripped
            start = search_from
        search_from = start + len(unit)
        return start, start + len(unit)

    def flush() -> None:
        if not buffer:
            return
        chunks.append(
            Chunk(
                content="\n\n".join(buffer).strip(),
                start_offset=offsets[0][0],
                end_offset=offsets[-1][1],
            )
        )

    for unit in units:
        span = locate(unit)

        # +2 for the "\n\n" that will join this unit to the previous one.
        addition = len(unit) + (2 if buffer else 0)
        if buffer and buffer_length + addition > size:
            flush()

            # Carry the tail of the finished chunk into the next one. Whole
            # units are kept rather than a raw character slice, so the overlap
            # is readable text rather than a fragment starting mid-word.
            carried: list[str] = []
            carried_offsets: list[tuple[int, int]] = []
            carried_length = 0
            for previous, previous_span in zip(
                reversed(buffer), reversed(offsets), strict=True
            ):
                if carried_length + len(previous) > overlap:
                    break
                carried.insert(0, previous)
                carried_offsets.insert(0, previous_span)
                carried_length += len(previous) + 2

            buffer = carried
            offsets = carried_offsets
            buffer_length = carried_length

        buffer.append(unit)
        offsets.append(span)
        buffer_length += addition

    flush()
    return chunks
