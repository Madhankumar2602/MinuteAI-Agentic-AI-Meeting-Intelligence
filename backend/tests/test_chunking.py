"""Transcript chunking: pure logic, counted in words so expectations are easy to read."""

import pytest

from app.services.embeddings.chunking import chunk_transcript
from tests.fakes import PLATFORM_SYNC


def words(texts: list[str]) -> list[int]:
    return [len(t.split()) for t in texts]


def turns(n: int, words_per_turn: int = 10) -> str:
    return "\n".join(
        f"Speaker{i}: " + " ".join(f"w{i}x{j}" for j in range(words_per_turn - 1)) for i in range(n)
    )


def test_empty_or_blank_transcript_has_no_chunks() -> None:
    assert chunk_transcript("", words) == []
    assert chunk_transcript(" \n\n \r\n", words) == []


def test_short_transcript_is_one_exact_chunk_without_surrounding_whitespace() -> None:
    text = "\n  Priya: Let's start.\nArjun: Agreed.  \n"
    [chunk] = chunk_transcript(text, words)
    assert chunk.content == "Priya: Let's start.\nArjun: Agreed."
    assert text[chunk.char_start : chunk.char_end] == chunk.content
    assert chunk.token_count == 5
    assert chunk.index == 0


def test_every_chunk_is_a_slice_within_budget_and_the_whole_transcript_is_covered() -> None:
    text = turns(40)  # 400 words
    chunks = chunk_transcript(text, words, max_tokens=50, overlap_tokens=10)

    assert len(chunks) > 8
    assert [c.index for c in chunks] == list(range(len(chunks)))
    covered = set()
    for c in chunks:
        assert text[c.char_start : c.char_end] == c.content
        assert c.token_count == len(c.content.split()) <= 50
        covered.update(range(c.char_start, c.char_end))
    assert all(i in covered for i, ch in enumerate(text) if not ch.isspace())


def test_chunks_break_between_turns_and_overlap_by_whole_turns() -> None:
    text = turns(12)  # 10-word turns
    chunks = chunk_transcript(text, words, max_tokens=40, overlap_tokens=10)

    for c in chunks:
        # Never starts or ends mid-turn.
        assert c.content.startswith("Speaker")
        assert c.char_end == len(text) or text[c.char_end] == "\n"
    for previous, current in zip(chunks, chunks[1:], strict=False):
        # The next chunk begins with the previous chunk's last turn.
        last_turn = previous.content.splitlines()[-1]
        assert current.content.splitlines()[0] == last_turn
        assert current.char_start < previous.char_end


def test_zero_overlap_produces_disjoint_chunks() -> None:
    chunks = chunk_transcript(turns(12), words, max_tokens=40, overlap_tokens=0)
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current.char_start > previous.char_end


def test_overlap_never_repeats_an_entire_chunk() -> None:
    # Big turns next to tiny ones: carrying must stop before the whole chunk.
    text = "A: one\nB: " + "x " * 30 + "\nC: two\nD: " + "y " * 30
    chunks = chunk_transcript(text, words, max_tokens=40, overlap_tokens=35)
    contents = [c.content for c in chunks]
    for previous, current in zip(contents, contents[1:], strict=False):
        assert not current.startswith(previous)


def test_a_long_turn_is_split_at_sentence_boundaries() -> None:
    sentence = "This sentence has exactly eight words in it."
    text = "Priya: " + " ".join([sentence] * 10)  # one 81-word turn
    chunks = chunk_transcript(text, words, max_tokens=30, overlap_tokens=0)

    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= 30
        assert c.content.endswith(".")  # cut after a sentence, not mid-sentence


def test_a_sentence_longer_than_the_budget_is_split_at_words() -> None:
    text = "Arjun: " + " ".join(f"word{i}" for i in range(100))  # no sentence end
    chunks = chunk_transcript(text, words, max_tokens=25, overlap_tokens=0)
    assert all(c.token_count <= 25 for c in chunks)
    assert " ".join(c.content for c in chunks).split() == text.split()


def test_an_unbroken_string_is_split_by_characters() -> None:
    def chars(texts: list[str]) -> list[int]:  # worst case: one token per character
        return [len(t) for t in texts]

    text = "Meera: " + "a" * 500
    chunks = chunk_transcript(text, chars, max_tokens=64, overlap_tokens=0)
    assert all(c.token_count <= 64 for c in chunks)
    assert "".join(c.content for c in chunks).replace(" ", "") == text.replace(" ", "")


def test_windows_line_endings_are_not_part_of_chunks() -> None:
    text = "Priya: first line.\r\nArjun: second line.\r\n"
    [chunk] = chunk_transcript(text, words)
    assert "\r" not in chunk.content.splitlines()[0]
    assert chunk.content.endswith("second line.")


def test_chunking_is_deterministic() -> None:
    assert chunk_transcript(PLATFORM_SYNC, words, max_tokens=60) == chunk_transcript(
        PLATFORM_SYNC, words, max_tokens=60
    )


@pytest.mark.parametrize(("max_tokens", "overlap"), [(0, 0), (10, 10), (10, -1)])
def test_invalid_parameters_are_rejected(max_tokens: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_transcript("A: hello", words, max_tokens=max_tokens, overlap_tokens=overlap)
