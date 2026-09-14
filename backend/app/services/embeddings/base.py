"""Provider-neutral embedding contract (M6, ADR 0012).

Indexing and search depend only on this module, as the extraction pipeline
depends only on ``llm/base.py``. Tests inject a deterministic fake; the real
implementation runs a sentence-transformers model locally with ONNX Runtime.
"""

from __future__ import annotations

from typing import Protocol

from app.core.exceptions import ServiceUnavailableError


class EmbeddingUnavailableError(ServiceUnavailableError):
    """The model files could not be downloaded or loaded.

    Treated as transient by the worker: the usual cause is a network failure
    while fetching the model the first time.
    """

    code = "embedding_unavailable"
    message = "The embedding model is not available right now."


class EmbeddingProvider(Protocol):
    # Identifies the exact model (name and pinned revision). Stored on every
    # chunk; vectors from different models are never compared.
    model: str
    dimensions: int
    # Longest input the model reads, in tokens, including special tokens.
    max_tokens: int

    def count_tokens(self, texts: list[str]) -> list[int]:
        """Model tokens in each text, excluding special tokens. Never truncates."""
        ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """One L2-normalised vector per text, in order."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """The vector for a search query."""
        ...

    def health_check(self) -> tuple[bool, str]:
        """Cheap status for /health/deps. Must not load or download the model."""
        ...
