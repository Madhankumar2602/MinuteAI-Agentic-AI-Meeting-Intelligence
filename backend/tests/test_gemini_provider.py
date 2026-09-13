"""GeminiProvider behaviour with the SDK stubbed out: no network, no quota.

Covers the parts that are easy to get wrong and expensive to discover in
production: which errors are retried, which are not, and how SDK exceptions
are translated to provider-neutral ones.
"""

import pytest
from google.genai import errors
from pydantic import BaseModel

import app.services.llm.gemini as gemini_module
from app.services.llm.base import (
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMRequestError,
    LLMResponseError,
    LLMUnavailableError,
)
from app.services.llm.gemini import GeminiProvider


class Answer(BaseModel):
    value: int


class _Usage:
    prompt_token_count = 11
    candidates_token_count = 3


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text
        self.candidates: list = []
        self.prompt_feedback = None
        self.usage_metadata = _Usage()


class _Models:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate_content(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _api_error(cls, code: int, status: str, message: str = "x"):
    return cls(code, {"error": {"code": code, "message": message, "status": status}})


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(gemini_module, "_BACKOFF_BASE_SECONDS", 0.0)


def _provider(outcomes: list, max_retries: int = 2) -> tuple[GeminiProvider, _Models]:
    provider = GeminiProvider(
        api_key="test-key", model="m", timeout_seconds=5, max_retries=max_retries
    )
    models = _Models(outcomes)
    provider._client = type("Client", (), {"aio": type("Aio", (), {"models": models})()})()
    return provider, models


async def _call(provider: GeminiProvider):
    return await provider.generate_structured(system_instruction="s", prompt="p", schema=Answer)


async def test_success_returns_validated_data_and_metadata() -> None:
    provider, _ = _provider([_Response('{"value": 42}')])
    result = await _call(provider)
    assert result.data.value == 42
    assert result.attempts == 1
    assert result.usage.input_tokens == 11
    assert result.provider == "gemini"


async def test_transient_503_is_retried() -> None:
    provider, models = _provider(
        [_api_error(errors.ServerError, 503, "UNAVAILABLE"), _Response('{"value": 1}')]
    )
    result = await _call(provider)
    assert result.data.value == 1
    assert models.calls == 2


async def test_persistent_429_raises_rate_limit_after_retry_budget() -> None:
    limited = _api_error(errors.ClientError, 429, "RESOURCE_EXHAUSTED")
    provider, models = _provider([limited, limited, limited], max_retries=2)
    with pytest.raises(LLMRateLimitError):
        await _call(provider)
    assert models.calls == 3  # 1 attempt + 2 retries


async def test_persistent_5xx_raises_unavailable() -> None:
    busy = _api_error(errors.ServerError, 500, "INTERNAL")
    provider, _ = _provider([busy, busy], max_retries=1)
    with pytest.raises(LLMUnavailableError):
        await _call(provider)


async def test_invalid_api_key_fails_fast_without_retry() -> None:
    bad_key = _api_error(errors.ClientError, 400, "INVALID_ARGUMENT", "API key not valid.")
    provider, models = _provider([bad_key, _Response('{"value": 1}')])
    with pytest.raises(LLMNotConfiguredError):
        await _call(provider)
    assert models.calls == 1


async def test_unavailable_model_is_a_configuration_error() -> None:
    provider, _ = _provider([_api_error(errors.ClientError, 404, "NOT_FOUND")])
    with pytest.raises(LLMNotConfiguredError):
        await _call(provider)


async def test_other_4xx_is_a_request_error() -> None:
    provider, models = _provider(
        [_api_error(errors.ClientError, 400, "INVALID_ARGUMENT", "bad schema")]
    )
    with pytest.raises(LLMRequestError):
        await _call(provider)
    assert models.calls == 1


async def test_schema_violation_is_retried_once_then_succeeds() -> None:
    provider, models = _provider(
        [_Response('{"value": "not a number"}'), _Response('{"value": 5}')]
    )
    assert (await _call(provider)).data.value == 5
    assert models.calls == 2


async def test_repeated_schema_violation_raises_response_error() -> None:
    provider, models = _provider([_Response('{"value": "a"}'), _Response('{"value": "b"}')])
    with pytest.raises(LLMResponseError) as exc_info:
        await _call(provider)
    assert exc_info.value.code == "llm_invalid_response"
    assert models.calls == 2


async def test_empty_response_raises_response_error() -> None:
    provider, _ = _provider([_Response(""), _Response("")])
    with pytest.raises(LLMResponseError):
        await _call(provider)


async def test_missing_api_key_raises_not_configured_and_reports_unhealthy() -> None:
    provider = GeminiProvider(api_key="", model="m", timeout_seconds=5, max_retries=1)
    with pytest.raises(LLMNotConfiguredError):
        await _call(provider)
    healthy, detail = await provider.health_check()
    assert healthy is False
    assert "not configured" in detail
