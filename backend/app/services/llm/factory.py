"""Builds the configured LLM provider.

``get_llm_provider`` is a FastAPI dependency, which is what lets tests swap in a
deterministic test double via ``app.dependency_overrides`` without patching.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.services.llm.base import LLMProvider
from app.services.llm.gemini import GeminiProvider


@lru_cache
def _build_provider() -> LLMProvider:
    # One provider today (ADR 0006). A second one becomes a branch on a
    # LLM_PROVIDER setting here, and nothing else changes.
    return GeminiProvider(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def get_llm_provider() -> LLMProvider:
    return _build_provider()
