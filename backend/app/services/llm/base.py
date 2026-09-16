"""Provider-neutral LLM contract.

Everything outside ``app/services/llm/`` depends only on this module. Swapping
Gemini for another provider means writing one new class that satisfies
``LLMProvider``. No route, pipeline, or test changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

from app.core.exceptions import BadGatewayError, ServiceUnavailableError

if TYPE_CHECKING:
    from app.schemas.transcription import TranscriptionResult

# --------------------------------------------------------------------------
# Errors
#
# They subclass AppError, so any of them escaping to FastAPI is rendered with
# the standard error envelope and a meaningful status code, with no extra
# handler. Callers that need to react (the pipeline marking a meeting FAILED)
# catch the shared LLMError base via the tuple below.
# --------------------------------------------------------------------------


class LLMNotConfiguredError(ServiceUnavailableError):
    """Missing/invalid API key, or the configured model is not available."""

    code = "llm_not_configured"
    message = "The AI provider is not configured correctly."


class LLMRateLimitError(ServiceUnavailableError):
    """Quota or rate limit still exceeded after retries."""

    code = "llm_rate_limited"
    message = "The AI provider's rate limit was reached. Try again shortly."


class LLMUnavailableError(ServiceUnavailableError):
    """5xx, timeout, or network failure that persisted through retries."""

    code = "llm_unavailable"
    message = "The AI provider is temporarily unavailable."


class LLMRequestError(BadGatewayError):
    """The provider rejected the request (non-retryable 4xx)."""

    code = "llm_request_rejected"
    message = "The AI provider rejected the request."


class LLMResponseError(BadGatewayError):
    """The provider answered, but not with usable output.

    Covers safety blocks, truncation, and JSON that does not validate against
    the requested schema.
    """

    code = "llm_invalid_response"
    message = "The AI provider returned an unusable response."


LLMError = (
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMUnavailableError,
    LLMRequestError,
    LLMResponseError,
)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LLMUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class StructuredResult[T: BaseModel]:
    """A schema-validated response plus the metadata needed for evaluation."""

    data: T
    provider: str
    model: str
    usage: LLMUsage
    latency_ms: int
    attempts: int


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


class LLMProvider(Protocol):
    name: str
    model: str

    async def generate_structured[T: BaseModel](
        self,
        *,
        system_instruction: str,
        prompt: str,
        schema: type[T],
        temperature: float = 0.1,
    ) -> StructuredResult[T]:
        """Return output that has ALREADY been validated against ``schema``.

        Implementations must raise one of the LLM*Error classes above, never a
        provider SDK exception. Callers must not need to know the vendor.
        """
        ...

    async def health_check(self) -> tuple[bool, str]:
        """Return (healthy, detail). Must never raise."""
        ...


class TranscriptionProvider(Protocol):
    """Speech-to-text contract (M4).

    Separate from ``LLMProvider`` because the two are genuinely independent
    choices: a dedicated speech-to-text model is a transcriber but not a
    language model.
    """

    name: str
    model: str

    async def transcribe(
        self, *, audio_path: Path, mime_type: str
    ) -> StructuredResult[TranscriptionResult]:
        """Return validated segments. Raises only the LLM*Error classes."""
        ...
