"""Retrieval smoke evaluation: does semantic search find the passage that answers a question?

    python -m app.evaluation.retrieval

Runs offline against the real embedding model and the two fixture transcripts.
Each question is worded differently from the transcript line that answers it
("signed out" vs "logged out"), so matching words alone is not enough.

For every chunk size it reports:

* hit@k - the answering line is inside one of the top k chunks
* MRR   - mean reciprocal rank of the first chunk containing it
* chance hit@1 - what picking a chunk at random would score

This is a smoke test, not the evaluation: 18 questions over 2 meetings is too
small to choose between chunk sizes (with a handful of chunks, large chunks win
by default). M12 evaluates retrieval properly on a larger labelled corpus.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.services.embeddings import get_embedder
from app.services.embeddings.chunking import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_OVERLAP_TOKENS,
    chunk_transcript,
)

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "transcripts"

# (question, meeting, text that must be inside the retrieved chunk)
QUESTIONS: list[tuple[str, str, str]] = [
    ("When are we upgrading the production database?", "platform_sync", "migrate the production database to PostgreSQL 16"),
    ("Is staging already on the new Postgres version?", "platform_sync", "staging database is fully migrated"),
    ("Who writes the migration runbook?", "platform_sync", "prepare the production migration runbook"),
    ("Why do people keep getting signed out?", "platform_sync", "Users are still getting logged out"),
    ("What is causing the token refresh failures?", "platform_sync", "clock on one API server has drifted"),
    ("Should we switch identity providers?", "platform_sync", "completely different auth provider"),
    ("Who is fixing the time sync on servers?", "platform_sync", "fix the clock synchronisation"),
    ("How do we release the API?", "platform_sync", "keeping blue-green deployment"),
    ("Who owns the docs update?", "platform_sync", "update the deployment documentation"),
    ("Did anyone try a prompt injection?", "platform_sync", "Ignore all previous instructions"),
    ("Who sends the spending report to accounting?", "platform_sync", "quarterly infrastructure cost report"),
    ("How many people did we interview?", "hiring_review", "interviewed four candidates"),
    ("Who is getting the job offer?", "hiring_review", "make an offer to Ananya"),
    ("Which candidate was weak on typing?", "hiring_review", "struggled with TypeScript generics"),
    ("Who checks the pay range with HR?", "hiring_review", "check the salary band with HR"),
    ("Who plans the new starter's first week?", "hiring_review", "prepare the onboarding checklist"),
    ("Is anything broken in the office?", "hiring_review", "coffee machine is broken"),
    ("Was accessibility discussed?", "hiring_review", "accessibility questions"),
]  # fmt: skip

CONFIGURATIONS = [(64, 12), (96, 16), (DEFAULT_MAX_TOKENS, DEFAULT_OVERLAP_TOKENS), (254, 48)]


def evaluate(max_tokens: int, overlap_tokens: int) -> dict[str, float]:
    embedder = get_embedder()
    chunks: list[tuple[str, str]] = []
    for name in sorted({meeting for _, meeting, _ in QUESTIONS}):
        text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
        for c in chunk_transcript(
            text, embedder.count_tokens, max_tokens=max_tokens, overlap_tokens=overlap_tokens
        ):
            chunks.append((name, c.content))

    similarity = (
        embedder.embed_sync([q for q, _, _ in QUESTIONS])
        @ embedder.embed_sync([content for _, content in chunks]).T
    )

    hit1 = hit3 = reciprocal = chance = 0.0
    for i, (_, meeting, answer) in enumerate(QUESTIONS):
        relevant = [
            j for j, (m, content) in enumerate(chunks) if m == meeting and answer in content
        ]
        assert relevant, f"no chunk contains {answer!r}"
        order = list(np.argsort(-similarity[i]))
        rank = min(order.index(j) for j in relevant) + 1
        hit1 += rank == 1
        hit3 += rank <= 3
        reciprocal += 1 / rank
        chance += len(relevant) / len(chunks)

    n = len(QUESTIONS)
    return {
        "chunks": len(chunks),
        "hit@1": hit1 / n,
        "hit@3": hit3 / n,
        "mrr": reciprocal / n,
        "chance@1": chance / n,
    }


def main() -> None:
    embedder = get_embedder()
    print(f"model: {embedder.model}   questions: {len(QUESTIONS)}")
    for max_tokens, overlap in CONFIGURATIONS:
        r = evaluate(max_tokens, overlap)
        marker = "  <- configured" if max_tokens == DEFAULT_MAX_TOKENS else ""
        print(
            f"max_tokens={max_tokens:3} overlap={overlap:2} chunks={r['chunks']:2}  "
            f"hit@1={r['hit@1']:.2f} hit@3={r['hit@3']:.2f} MRR={r['mrr']:.3f} "
            f"(chance@1={r['chance@1']:.2f}){marker}"
        )


if __name__ == "__main__":
    main()
