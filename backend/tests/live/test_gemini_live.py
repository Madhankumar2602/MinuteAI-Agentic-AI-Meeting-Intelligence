"""Opt-in tests against the REAL Gemini API.

Skipped by default: they need a key, spend quota, take tens of seconds, and
their exact output can vary between runs. Run them deliberately:

    RUN_LIVE_LLM_TESTS=1 pytest -m live

Assertions target properties a correct extraction must have, not exact wording.
"""

import os
from datetime import UTC, date, datetime

import pytest

from app.core.config import settings
from app.schemas.extraction import MeetingExtraction
from app.services.intelligence import normalise_extraction
from app.services.llm.gemini import GeminiProvider
from app.services.prompts import EXTRACTION_SYSTEM_INSTRUCTION, build_extraction_prompt
from tests.fakes import PLATFORM_SYNC

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_LLM_TESTS") != "1",
        reason="set RUN_LIVE_LLM_TESTS=1 to call the real API",
    ),
]


@pytest.fixture
def provider() -> GeminiProvider:
    key = settings.gemini_api_key.get_secret_value()
    if not key:
        pytest.skip("GEMINI_API_KEY not set")
    return GeminiProvider(
        api_key=key,
        model=settings.gemini_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


async def test_live_health_check(provider: GeminiProvider) -> None:
    healthy, detail = await provider.health_check()
    assert healthy, detail


async def test_live_extraction_of_platform_sync(provider: GeminiProvider) -> None:
    result = await provider.generate_structured(
        system_instruction=EXTRACTION_SYSTEM_INSTRUCTION,
        prompt=build_extraction_prompt(
            title="Platform sync",
            meeting_date=datetime(2026, 9, 10, 10, tzinfo=UTC),
            transcript=PLATFORM_SYNC,
        ),
        schema=MeetingExtraction,
    )
    n = normalise_extraction(result.data, transcript=PLATFORM_SYNC, meeting_date=date(2026, 9, 10))

    owners = {a.owner_name for a in n.action_items}
    assert {"Karthik", "Meera", "Arjun"} <= owners

    by_owner = {a.owner_name: a for a in n.action_items}
    assert by_owner["Meera"].deadline == date(2026, 9, 11)  # "tomorrow"
    assert by_owner["Arjun"].deadline == date(2026, 9, 30)  # "end of the month"
    assert by_owner["Meera"].priority is not None  # explicitly "urgent"

    # The prompt-injection line must not become an action item.
    assert not any("password" in a.task.lower() for a in n.action_items)

    assert 2 <= len(n.decisions) <= 3
    verified = sum(d.evidence_verified for d in n.decisions) + sum(
        a.evidence_verified for a in n.action_items
    )
    total = len(n.decisions) + len(n.action_items)
    assert verified / total >= 0.8
