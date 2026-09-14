"""Split a transcript into passages sized for the embedding model.

Pure function, no I/O. Token counts come from a callable, so tests can use a
simple word counter while production uses the model's own tokenizer.

    transcript ─► speaker turns (lines)
               ─► a turn longer than the budget is split at sentence ends,
                  then at word boundaries, then (a pathological unbroken
                  string) at fixed character widths
               ─► pieces packed greedily into chunks of at most
                  ``max_tokens`` tokens, each chunk starting with the tail of
                  the previous one (up to ``overlap_tokens``) so an exchange
                  that straddles a boundary is still retrievable in one piece

Every chunk is an exact slice of the transcript (``text[char_start:char_end]``),
so results can cite where they came from.

Why speaker turns: a turn is the natural unit of meaning in a meeting. Cutting
at fixed character offsets splits sentences and names in half, which hurts
both retrieval and the readability of cited passages.

Token counts of pieces are added rather than recounted: the WordPiece
tokenizer splits on whitespace first, so pieces separated by whitespace
tokenize independently (verified against the real tokenizer in the tests).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# Changing the algorithm or its parameters changes the chunks, so bump this and
# existing meetings are re-indexed the next time they are processed.
CHUNKER_VERSION = "turns-v1"

# all-MiniLM-L6-v2 reads 256 tokens (2 of them special) but was trained on
# passages of up to 128. 160 keeps chunks well inside the limit, focused enough
# to retrieve precisely, and large enough to carry a few turns of context.
DEFAULT_MAX_TOKENS = 160
DEFAULT_OVERLAP_TOKENS = 32

TokenCounter = Callable[[list[str]], list[int]]

_LINE = re.compile(r"[^\n]+")
# A sentence ends at . ! or ? followed by whitespace, or at the end of the line.
_SENTENCE = re.compile(r"\S.*?(?:[.!?](?=\s)|$)")
_WORD = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    content: str
    char_start: int
    char_end: int
    token_count: int


@dataclass(frozen=True, slots=True)
class _Piece:
    start: int
    end: int
    tokens: int


def _spans(pattern: re.Pattern[str], text: str, start: int, end: int) -> list[tuple[int, int]]:
    out = []
    for m in pattern.finditer(text, start, end):
        s, e = m.start(), m.end()
        # Trim whitespace inside the match so spans never begin or end with it.
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if s < e:
            out.append((s, e))
    return out


def _pieces(text: str, count: TokenCounter, max_tokens: int) -> list[_Piece]:
    """Units no larger than ``max_tokens``, in transcript order."""
    lines = _spans(_LINE, text, 0, len(text))
    tokens = count([text[s:e] for s, e in lines])
    pieces: list[_Piece] = []
    for (s, e), n in zip(lines, tokens, strict=True):
        if n <= max_tokens:
            pieces.append(_Piece(s, e, n))
        else:
            pieces.extend(_split(text, s, e, count, max_tokens))
    return pieces


def _split(text: str, start: int, end: int, count: TokenCounter, max_tokens: int) -> list[_Piece]:
    """Break one over-long turn: sentences, then words, then characters."""
    for pattern in (_SENTENCE, _WORD):
        spans = _spans(pattern, text, start, end)
        if len(spans) > 1:
            out: list[_Piece] = []
            for (s, e), n in zip(spans, count([text[s:e] for s, e in spans]), strict=True):
                out.extend(
                    [_Piece(s, e, n)] if n <= max_tokens else _split(text, s, e, count, max_tokens)
                )
            return _merge(out, max_tokens)
    # A single unbroken string longer than the budget (e.g. a pasted hash).
    # Half the budget in characters keeps each slice within it even when every
    # character becomes its own token, and unknown characters collapse further.
    width = max(max_tokens // 2, 1)
    slices = [(s, min(s + width, end)) for s in range(start, end, width)]
    return [
        _Piece(s, e, n)
        for (s, e), n in zip(slices, count([text[s:e] for s, e in slices]), strict=True)
    ]


def _merge(pieces: list[_Piece], max_tokens: int) -> list[_Piece]:
    """Rejoin adjacent sentence/word pieces up to the budget.

    Without this, a long turn would become one piece per word and the packing
    step would still work, but overlap would be measured in single words.
    """
    merged: list[_Piece] = []
    for p in pieces:
        if merged and merged[-1].tokens + p.tokens <= max_tokens:
            last = merged[-1]
            merged[-1] = _Piece(last.start, p.end, last.tokens + p.tokens)
        else:
            merged.append(p)
    return merged


def chunk_transcript(
    text: str,
    count_tokens: TokenCounter,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    if max_tokens < 1 or not 0 <= overlap_tokens < max_tokens:
        raise ValueError("need max_tokens >= 1 and 0 <= overlap_tokens < max_tokens")

    chunks: list[Chunk] = []
    current: list[_Piece] = []

    def emit() -> None:
        chunks.append(
            Chunk(
                index=len(chunks),
                content=text[current[0].start : current[-1].end],
                char_start=current[0].start,
                char_end=current[-1].end,
                token_count=sum(p.tokens for p in current),
            )
        )

    for piece in _pieces(text, count_tokens, max_tokens):
        if current and sum(p.tokens for p in current) + piece.tokens > max_tokens:
            emit()
            # Carry the previous chunk's closing pieces into the next one, as
            # long as they fit the overlap budget and leave room for this piece.
            # The whole previous chunk can never be carried: it did not fit
            # together with this piece, so it exceeds ``max_tokens - piece``.
            carried: list[_Piece] = []
            budget = min(overlap_tokens, max_tokens - piece.tokens)
            for p in reversed(current):
                if sum(c.tokens for c in carried) + p.tokens > budget:
                    break
                carried.insert(0, p)
            current = carried
        current.append(piece)

    if current:
        emit()
    return chunks
