"""Transcript chunking, local embeddings, indexing, and semantic search (M6)."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.services.embeddings.base import EmbeddingProvider, EmbeddingUnavailableError

__all__ = ["EmbeddingProvider", "EmbeddingUnavailableError", "get_embedder"]


@lru_cache(maxsize=1)
def get_embedder() -> EmbeddingProvider:
    """The process-wide embedder (a FastAPI dependency, overridden in tests).

    One instance per process: the model is ~90 MB and loads once, on first use,
    rather than at start-up, so the API starts quickly even before the model has
    been downloaded.
    """
    from app.services.embeddings.minilm import MiniLMEmbedder

    return MiniLMEmbedder(
        model_id=settings.embedding_model,
        revision=settings.embedding_model_revision,
        batch_size=settings.embedding_batch_size,
    )
