"""Evidence verification: is the quote the model cited really in the transcript?

Language models sometimes produce plausible action items or decisions that
nobody actually said. Asking for a verbatim ``evidence_quote`` and then checking
it mechanically gives each extracted item a cheap, deterministic grounding
signal that does not depend on trusting the model.

The check is intentionally tolerant of trivial differences (case, punctuation,
whitespace, a dropped filler word) but not of paraphrase:

1. Normalise both texts to lower-case alphanumeric tokens, joining
   contractions ("I'll" and "Ill" both become ``ill``).
2. Accept if the normalised quote occurs contiguously in the transcript.
3. Otherwise find an *anchor*: the longest contiguous run of matching tokens,
   which must be at least ``MIN_TOKENS`` long. Then compare the quote against
   only the transcript window around that anchor, and accept if at least
   ``MIN_COVERAGE`` of the quote's tokens match there.

The window matters: counting matches across the whole transcript would let a
paraphrase "verify" by collecting common words ("the", "on", "we") from
unrelated sentences.

``evidence_verified=False`` does not delete an item. It is surfaced to the user,
since a false negative (a real item with a loosely
quoted excerpt) is more costly than showing an unverified flag.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

MIN_TOKENS = 4
MIN_COVERAGE = 0.8

_TOKEN = re.compile(r"[0-9a-z]+")
_APOSTROPHES = re.compile(r"['’‘]")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(_APOSTROPHES.sub("", text.casefold()))


class TranscriptIndex:
    """Pre-tokenised transcript, built once per processing run."""

    def __init__(self, transcript: str) -> None:
        self.tokens = tokenize(transcript)
        # Space-joined form makes the exact-match fast path a substring test.
        self._joined = f" {' '.join(self.tokens)} "

    def supports(self, quote: str | None) -> bool:
        if not quote:
            return False
        q = tokenize(quote)
        if len(q) < MIN_TOKENS:
            # Too short to be meaningful evidence: "yes, okay" matches anything.
            return False

        if f" {' '.join(q)} " in self._joined:
            return True

        anchor = SequenceMatcher(None, self.tokens, q, autojunk=False).find_longest_match(
            0, len(self.tokens), 0, len(q)
        )
        if anchor.size < MIN_TOKENS:
            return False

        # Transcript window aligned to where the quote would start, padded by
        # half the quote length on each side to allow for inserted/dropped words.
        pad = len(q) // 2 + 1
        start = max(0, anchor.a - anchor.b - pad)
        end = min(len(self.tokens), anchor.a + (len(q) - anchor.b) + pad)
        window = SequenceMatcher(None, self.tokens[start:end], q, autojunk=False)
        matched = sum(block.size for block in window.get_matching_blocks())
        return matched / len(q) >= MIN_COVERAGE
