"""Tests for text chunking.

Pure logic, no database and no model calls, so these are fast and exhaustive.
"""

from __future__ import annotations

import pytest

from app.services.chunking import chunk_text


def contents(text: str, *, size: int = 100, overlap: int = 20) -> list[str]:
    return [chunk.content for chunk in chunk_text(text, size=size, overlap=overlap)]


class TestValidation:
    @pytest.mark.parametrize(
        ("size", "overlap"),
        [
            (0, 0),
            (-1, 0),
            (100, -1),
            # Each chunk would start at or before the previous one, so the loop
            # would never advance -- failing loudly beats hanging.
            (100, 100),
            (100, 150),
        ],
    )
    def test_rejects_impossible_settings(self, size: int, overlap: int) -> None:
        with pytest.raises(ValueError):
            chunk_text("some text", size=size, overlap=overlap)

    @pytest.mark.parametrize("text", ["", "   ", "\n\n\t  \n"])
    def test_empty_text_produces_no_chunks(self, text: str) -> None:
        assert chunk_text(text, size=100, overlap=20) == []


class TestSplitting:
    def test_short_text_is_one_chunk(self) -> None:
        assert contents("A short document.") == ["A short document."]

    def test_splits_on_paragraph_boundaries(self) -> None:
        text = "\n\n".join(["A" * 60, "B" * 60, "C" * 60])

        result = contents(text, size=100, overlap=0)

        # Each paragraph is 60 characters, so two cannot share a 100-character
        # chunk. Splitting on structure keeps them whole rather than cutting
        # one in half at character 100.
        assert result == ["A" * 60, "B" * 60, "C" * 60]

    def test_packs_several_small_paragraphs_into_one_chunk(self) -> None:
        text = "\n\n".join(["one", "two", "three"])

        assert contents(text, size=100, overlap=0) == ["one\n\ntwo\n\nthree"]

    def test_falls_back_to_sentences_for_a_long_paragraph(self) -> None:
        sentence = "This sentence is exactly long enough to matter here. "
        text = sentence * 5

        result = contents(text, size=120, overlap=0)

        assert len(result) > 1
        # Split at sentence ends, so no chunk begins mid-sentence.
        assert all(chunk.startswith("This sentence") for chunk in result)

    def test_cuts_a_run_that_has_no_boundary_at_all(self) -> None:
        """A base64 blob or a minified stylesheet has no sentence structure,
        so any split point is equally arbitrary -- but it must still split."""
        text = "x" * 350

        result = contents(text, size=100, overlap=0)

        assert len(result) == 4
        assert all(len(chunk) <= 100 for chunk in result)

    def test_no_chunk_greatly_exceeds_the_target_size(self) -> None:
        text = "\n\n".join(f"Paragraph number {index}. " * 4 for index in range(20))

        for chunk in chunk_text(text, size=200, overlap=40):
            # A single indivisible unit may reach the limit, never blow past it.
            assert len(chunk.content) <= 200

    def test_loses_no_words(self) -> None:
        """Whatever the split points, every word must survive somewhere --
        silently dropping text would make a document partly unsearchable."""
        text = "\n\n".join(f"Paragraph {index} has some words in it." for index in range(30))

        joined = " ".join(contents(text, size=150, overlap=0))

        for index in range(30):
            assert f"Paragraph {index} " in joined


class TestOverlap:
    def test_consecutive_chunks_share_text(self) -> None:
        """A fact straddling a boundary would otherwise be findable from
        neither side."""
        text = "\n\n".join(f"Sentence number {index}." for index in range(12))

        result = contents(text, size=90, overlap=40)

        assert len(result) > 1
        first_tail = result[0].split("\n\n")[-1]
        assert first_tail in result[1]

    def test_zero_overlap_repeats_nothing(self) -> None:
        text = "\n\n".join(["A" * 50, "B" * 50, "C" * 50])

        result = contents(text, size=60, overlap=0)

        assert result == ["A" * 50, "B" * 50, "C" * 50]

    def test_overlap_carries_whole_units_not_fragments(self) -> None:
        """Carrying a raw character slice would start a chunk mid-word."""
        text = "\n\n".join(f"Sentence number {index} is here." for index in range(10))

        for chunk in contents(text, size=100, overlap=40):
            assert chunk.startswith("Sentence")


class TestOffsets:
    def test_offsets_point_at_the_original_text(self) -> None:
        text = "\n\n".join(["First paragraph.", "Second paragraph.", "Third one."])

        for chunk in chunk_text(text, size=20, overlap=0):
            located = text[chunk.start_offset : chunk.end_offset]
            # The offsets must delimit the passage in the source document, so a
            # citation can point at where it came from.
            assert chunk.content.split("\n\n")[0] in located

    def test_offsets_advance(self) -> None:
        text = "\n\n".join(f"Paragraph {index}." for index in range(10))

        chunks = chunk_text(text, size=40, overlap=0)

        for earlier, later in zip(chunks, chunks[1:], strict=False):
            assert later.start_offset >= earlier.start_offset

    def test_a_repeated_sentence_gets_distinct_offsets(self) -> None:
        """Naively searching for the text would report the first occurrence
        every time, so every copy would cite the same place."""
        text = "\n\n".join(["Same line."] * 4)

        chunks = chunk_text(text, size=15, overlap=0)

        starts = [chunk.start_offset for chunk in chunks]
        assert len(set(starts)) == len(starts)


class TestRealisticInput:
    def test_handles_a_flattened_table(self) -> None:
        """What the DOCX and XLSX extractors produce: rows separated by
        newlines rather than blank lines."""
        text = "\n".join(f"Region {index} | {index * 1000}" for index in range(50))

        result = chunk_text(text, size=200, overlap=40)

        assert len(result) > 1
        assert all(chunk.content.strip() for chunk in result)

    def test_handles_text_with_no_trailing_newline(self) -> None:
        assert contents("One paragraph with no newline at the end") == [
            "One paragraph with no newline at the end"
        ]

    def test_preserves_unicode(self) -> None:
        text = "Grüße — naïve café 日本語 🎉"

        assert contents(text) == [text]
