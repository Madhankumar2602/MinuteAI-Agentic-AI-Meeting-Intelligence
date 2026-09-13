"""Google Gemini implementation of ``LLMProvider``.

Responsibilities, all of which stay inside this file:

* translating a Pydantic schema into Gemini's constrained JSON output mode
* retrying transient failures (429, 5xx, network) with exponential backoff
* mapping SDK exceptions onto the provider-neutral LLM*Error classes
* validating the returned JSON locally, whatever the provider promised
* logging call metadata (model, attempts, latency, tokens) but never content
"""

from __future__ import annotations

import asyncio
import random
import time

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from app.services.llm.base import (
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMRequestError,
    LLMResponseError,
    LLMUnavailableError,
    LLMUsage,
    StructuredResult,
)

logger = get_logger(__name__)

# Backoff: 1s, 2s, 4s ... capped, plus jitter so that concurrent callers that
# failed together do not all retry in the same instant.
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 20.0

# Finish reasons that mean "there is no complete answer to parse".
_TRUNCATED = {types.FinishReason.MAX_TOKENS}
_BLOCKED = {
    types.FinishReason.SAFETY,
    types.FinishReason.RECITATION,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.SPII,
}


class _RetryableError(Exception):
    """Internal marker: this attempt failed in a way worth retrying."""

    def __init__(self, final: Exception) -> None:
        self.final = final
        super().__init__(str(final))


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self.model = model
        self._api_key_present = bool(api_key)
        self._max_retries = max_retries
        # Construct lazily-failing: an app with no key must still start, serve
        # CRUD endpoints, and report "not configured" from /health/deps.
        self._client = (
            genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
            )
            if api_key
            else None
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_structured[T: BaseModel](
        self,
        *,
        system_instruction: str,
        prompt: str,
        schema: type[T],
        temperature: float = 0.1,
    ) -> StructuredResult[T]:
        client = self._require_client()

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
            # Automatic function calling is irrelevant for extraction and emits
            # a warning when left on; the M8 agent will manage tools explicitly.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        started = time.perf_counter()
        attempts = 0
        last_error: Exception | None = None

        # One extra attempt beyond the retry budget is allowed for invalid
        # JSON: a single bad sample is common and cheap to re-roll, but a model
        # that repeatedly cannot satisfy the schema is a genuine failure.
        schema_retries_left = 1

        while attempts <= self._max_retries:
            attempts += 1
            try:
                try:
                    response = await client.aio.models.generate_content(
                        model=self.model, contents=prompt, config=config
                    )
                except Exception as exc:
                    translated = _translate(exc)
                    if translated is exc:
                        raise
                    raise translated from exc
                data = self._parse(response, schema)
            except _RetryableError as exc:
                last_error = exc.final
            except LLMResponseError as exc:
                if schema_retries_left > 0 and exc.code == "llm_invalid_response":
                    schema_retries_left -= 1
                    last_error = exc
                    logger.warning(
                        "llm response failed validation, retrying",
                        extra={"provider": self.name, "model": self.model, "attempt": attempts},
                    )
                    continue
                raise
            else:
                usage = response.usage_metadata
                result = StructuredResult(
                    data=data,
                    provider=self.name,
                    model=self.model,
                    usage=LLMUsage(
                        input_tokens=getattr(usage, "prompt_token_count", None),
                        output_tokens=getattr(usage, "candidates_token_count", None),
                    ),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    attempts=attempts,
                )
                logger.info(
                    "llm call succeeded",
                    extra={
                        "provider": self.name,
                        "model": self.model,
                        "schema": schema.__name__,
                        "attempts": attempts,
                        "latency_ms": result.latency_ms,
                        "input_tokens": result.usage.input_tokens,
                        "output_tokens": result.usage.output_tokens,
                    },
                )
                return result

            if attempts <= self._max_retries:
                delay = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * 2 ** (attempts - 1))
                delay += random.uniform(0, delay / 2)
                logger.warning(
                    "llm call failed, backing off",
                    extra={
                        "provider": self.name,
                        "model": self.model,
                        "attempt": attempts,
                        "error": type(last_error).__name__,
                        "retry_in_s": round(delay, 2),
                    },
                )
                await asyncio.sleep(delay)

        logger.error(
            "llm call exhausted retries",
            extra={"provider": self.name, "model": self.model, "attempts": attempts},
        )
        assert last_error is not None
        raise last_error

    async def health_check(self) -> tuple[bool, str]:
        """Checks configuration and that the model's metadata can be fetched.

        Deliberately does NOT generate content: that would spend quota every
        time a probe runs. A successful metadata lookup proves the key is
        accepted, though not that generation quota remains.
        """
        if self._client is None:
            return False, "not configured (GEMINI_API_KEY is empty)"
        try:
            await self._client.aio.models.get(model=self.model)
            return True, f"reachable (model={self.model})"
        except genai_errors.APIError as exc:
            return False, f"{exc.code} {exc.status}"
        except Exception as exc:  # noqa: BLE001 - health checks never raise
            return False, type(exc).__name__

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_client(self) -> genai.Client:
        if self._client is None:
            raise LLMNotConfiguredError("GEMINI_API_KEY is not set.")
        return self._client

    def _parse[T: BaseModel](self, response: types.GenerateContentResponse, schema: type[T]) -> T:
        """Turn a raw response into validated data or a precise error."""
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise LLMResponseError(
                "The AI provider blocked the input.",
                details={"block_reason": str(feedback.block_reason)},
                code="llm_blocked",
            )

        candidates = response.candidates or []
        finish = candidates[0].finish_reason if candidates else None
        if finish in _BLOCKED:
            raise LLMResponseError(
                "The AI provider blocked the output.",
                details={"finish_reason": str(finish)},
                code="llm_blocked",
            )
        if finish in _TRUNCATED:
            raise LLMResponseError(
                "The AI response was truncated before completion.",
                details={"finish_reason": str(finish)},
                code="llm_truncated",
            )

        text = response.text
        if not text:
            raise LLMResponseError("The AI provider returned an empty response.")

        try:
            # Validate locally even though a response schema was requested:
            # constrained decoding reduces malformed output but does not
            # guarantee it, and this is the trust boundary.
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMResponseError(
                "The AI response did not match the expected structure.",
                details={"validation_errors": exc.error_count()},
            ) from exc


def _translate(exc: Exception) -> Exception:
    """Map an SDK / transport exception to a provider-neutral one.

    Returns a ``_RetryableError`` wrapper when the failure is transient.
    """
    if isinstance(exc, genai_errors.APIError):
        code = exc.code or 0
        message = (exc.message or "").lower()
        if code == 429:
            return _RetryableError(LLMRateLimitError(details={"provider_status": exc.status}))
        if code >= 500:
            return _RetryableError(LLMUnavailableError(details={"provider_status": exc.status}))
        if code in (401, 403) or (code == 400 and "api key" in message):
            return LLMNotConfiguredError(
                "The AI provider rejected the API key.",
                details={"provider_status": exc.status},
            )
        if code == 404:
            return LLMNotConfiguredError(
                "The configured AI model is not available to this API key.",
                details={"provider_status": exc.status},
            )
        return LLMRequestError(details={"provider_code": code, "provider_status": exc.status})
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, asyncio.TimeoutError)):
        return _RetryableError(LLMUnavailableError(details={"transport_error": type(exc).__name__}))
    return exc
