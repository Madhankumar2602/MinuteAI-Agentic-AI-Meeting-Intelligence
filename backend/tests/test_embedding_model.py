"""The real embedding model (downloads ~90 MB once, then runs offline from cache).

These tests prove that the ONNX implementation is the model, not an
approximation of it: vectors match ones produced independently by the
reference ``sentence-transformers`` library (PyTorch), recorded in
``fixtures/embeddings/minilm_reference.json``.
"""

import json

import numpy as np
import pytest

from app.core.config import settings
from app.services.embeddings.chunking import DEFAULT_MAX_TOKENS, chunk_transcript
from app.services.embeddings.minilm import MiniLMEmbedder
from tests.fakes import FIXTURES, PLATFORM_SYNC

REFERENCE = json.loads(
    (FIXTURES / "embeddings" / "minilm_reference.json").read_text(encoding="utf-8")
)


@pytest.fixture(scope="module")
def embedder() -> MiniLMEmbedder:
    return MiniLMEmbedder(
        model_id=settings.embedding_model, revision=settings.embedding_model_revision
    )


def test_reference_was_generated_from_the_configured_model() -> None:
    assert REFERENCE["model"] == settings.embedding_model
    assert REFERENCE["revision"] == settings.embedding_model_revision


def test_vectors_match_sentence_transformers(embedder: MiniLMEmbedder) -> None:
    ours = embedder.embed_sync(REFERENCE["texts"])
    reference = np.array(REFERENCE["vectors"], dtype=np.float32)

    assert ours.shape == (len(REFERENCE["texts"]), 384)
    # Covers a normal sentence, a one-word text, non-ASCII text, and a text
    # longer than 256 tokens (so truncation must match too).
    assert np.abs(ours - reference).max() < 1e-4
    assert np.allclose(np.linalg.norm(ours, axis=1), 1.0, atol=1e-5)


def test_batching_does_not_change_vectors(embedder: MiniLMEmbedder) -> None:
    texts = REFERENCE["texts"]
    one_by_one = np.vstack([embedder.embed_sync([t]) for t in texts])
    batched = MiniLMEmbedder(
        model_id=settings.embedding_model, revision=settings.embedding_model_revision, batch_size=2
    ).embed_sync(texts)
    # Padding in a batch must not leak into the mean (attention-mask pooling).
    assert np.abs(one_by_one - batched).max() < 1e-5


def test_meaning_not_wording_drives_similarity(embedder: MiniLMEmbedder) -> None:
    query, paraphrase, unrelated = embedder.embed_sync(
        [
            "When is the database being upgraded?",
            "We will migrate production to PostgreSQL 16 on Sunday.",
            "Arjun will send the cost report to finance.",
        ]
    )
    assert float(query @ paraphrase) > float(query @ unrelated) + 0.2


def test_token_counts_add_up_across_whitespace(embedder: MiniLMEmbedder) -> None:
    """The chunker adds piece counts instead of recounting; check that is exact."""
    lines = [line for line in PLATFORM_SYNC.splitlines() if line.strip()]
    assert sum(embedder.count_tokens(lines)) == embedder.count_tokens(["\n".join(lines)])[0]


def test_real_chunks_fit_the_model_without_truncation(embedder: MiniLMEmbedder) -> None:
    long_transcript = "\n".join([PLATFORM_SYNC] * 6)
    chunks = chunk_transcript(long_transcript, embedder.count_tokens)

    assert len(chunks) > 3
    recounted = embedder.count_tokens([c.content for c in chunks])
    for chunk, actual in zip(chunks, recounted, strict=True):
        assert chunk.token_count == actual <= DEFAULT_MAX_TOKENS
        assert actual + 2 <= embedder.max_tokens  # [CLS] and [SEP]
