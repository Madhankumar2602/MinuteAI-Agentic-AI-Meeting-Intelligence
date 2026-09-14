"""Test doubles. Used ONLY by the test suite, never by application code.

Tests must be deterministic, free, and runnable offline, so they cannot call
the real Gemini API. ``FakeLLMProvider`` satisfies the same ``LLMProvider``
contract and is injected through FastAPI's dependency overrides. The real
provider's behaviour is covered separately by ``test_gemini_provider.py``
(offline, SDK stubbed) and ``tests/live/`` (opt-in, real API).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from app.schemas.extraction import ExtractedActionItem, ExtractedDecision, MeetingExtraction
from app.services.llm.base import LLMUsage, StructuredResult

FIXTURES = Path(__file__).parent / "fixtures"
PLATFORM_SYNC = (FIXTURES / "transcripts" / "platform_sync.txt").read_text(encoding="utf-8")


def platform_sync_extraction() -> MeetingExtraction:
    """What a correct extraction of ``platform_sync.txt`` looks like.

    Quotes are copied from the fixture, so evidence verification runs against
    real text rather than being stubbed.
    """
    return MeetingExtraction(
        summary="The team agreed to migrate production to PostgreSQL 16 and keep blue-green deployment.",
        key_points=["Staging is on PostgreSQL 16", "Auth logouts caused by clock drift"],
        participants=["Priya", "Arjun", "Meera", "Karthik"],
        decisions=[
            ExtractedDecision(
                decision="Migrate the production database to PostgreSQL 16 on the Sunday maintenance window.",
                context="Staging migration succeeded with all tests passing.",
                evidence_quote="We will migrate the production database to PostgreSQL 16 on the Sunday maintenance window.",
            ),
            ExtractedDecision(
                decision="Keep blue-green deployment for the API.",
                context=None,
                evidence_quote="so we are keeping blue-green deployment for the API",
            ),
        ],
        action_items=[
            ExtractedActionItem(
                task="Prepare the production migration runbook",
                owner="Karthik",
                deadline="2026-09-16",
                deadline_text="by next Wednesday",
                priority=None,
                evidence_quote="I'll have the runbook ready by next Wednesday.",
            ),
            ExtractedActionItem(
                task="Fix the clock synchronisation on the API servers",
                owner="Meera",
                deadline="2026-09-11",
                deadline_text="tomorrow",
                priority="high",
                evidence_quote="please fix the clock synchronisation on the API servers",
            ),
            ExtractedActionItem(
                task="Send the quarterly infrastructure cost report to finance",
                owner="Arjun",
                deadline="2026-09-30",
                deadline_text="by the end of the month",
                priority=None,
                evidence_quote="send the quarterly infrastructure cost report to finance by the end of the month",
            ),
        ],
    )


class FakeLLMProvider:
    name = "fake"
    model = "fake-model-1"

    def __init__(
        self,
        extraction: MeetingExtraction | None = None,
        error: Exception | None = None,
        healthy: bool = True,
    ) -> None:
        self.extraction = extraction or platform_sync_extraction()
        self.error = error
        self.healthy = healthy
        self.calls: list[dict] = []

    async def generate_structured[T: BaseModel](
        self,
        *,
        system_instruction: str,
        prompt: str,
        schema: type[T],
        temperature: float = 0.1,
    ) -> StructuredResult[T]:
        self.calls.append({"prompt": prompt, "schema": schema.__name__})
        if self.error is not None:
            raise self.error
        # Round-trip through JSON so the fake exercises the same validation
        # path as a real provider response.
        data = schema.model_validate_json(self.extraction.model_dump_json())
        return StructuredResult(
            data=data,
            provider=self.name,
            model=self.model,
            usage=LLMUsage(input_tokens=900, output_tokens=450),
            latency_ms=5,
            attempts=1,
        )

    async def health_check(self) -> tuple[bool, str]:
        return self.healthy, "fake provider"


# ---------------------------------------------------------------------------
# Transcription (M4)
# ---------------------------------------------------------------------------

from app.schemas.transcription import TranscriptionResult, TranscriptSegment  # noqa: E402

AUDIO_FIXTURE = FIXTURES / "audio" / "platform_sync.wav"

# Smallest well-formed WAV header plus a little silence: enough for signature
# sniffing and the wave module, small enough to upload in every test.
TINY_WAV = (
    b"RIFF"
    + (36 + 1600).to_bytes(4, "little")
    + b"WAVE"
    + b"fmt "
    + (16).to_bytes(4, "little")
    + (1).to_bytes(2, "little")
    + (1).to_bytes(2, "little")
    + (8000).to_bytes(4, "little")
    + (16000).to_bytes(4, "little")
    + (2).to_bytes(2, "little")
    + (16).to_bytes(2, "little")
    + b"data"
    + (1600).to_bytes(4, "little")
    + b"\x00" * 1600
)


def platform_sync_transcription() -> TranscriptionResult:
    """What transcribing the platform_sync recording should produce."""
    segments = []
    for i, line in enumerate(PLATFORM_SYNC.splitlines()):
        speaker, _, text = line.partition(":")
        segments.append(
            TranscriptSegment(speaker=speaker.strip(), start_seconds=i * 5.0, text=text.strip())
        )
    return TranscriptionResult(language="en", segments=segments)


class FakeTranscriber:
    name = "fake"
    model = "fake-transcribe-1"

    def __init__(
        self, result: TranscriptionResult | None = None, error: Exception | None = None
    ) -> None:
        self.result = result or platform_sync_transcription()
        self.error = error
        self.calls: list[dict] = []

    async def transcribe(
        self, *, audio_path, mime_type: str
    ) -> StructuredResult[TranscriptionResult]:
        # Record what reached the transcriber: proves the worker really
        # downloaded the object from storage to a local file first.
        self.calls.append(
            {"mime_type": mime_type, "size": audio_path.stat().st_size, "suffix": audio_path.suffix}
        )
        if self.error is not None:
            raise self.error
        return StructuredResult(
            data=TranscriptionResult.model_validate(self.result.model_dump()),
            provider=self.name,
            model=self.model,
            usage=LLMUsage(input_tokens=4000, output_tokens=1300),
            latency_ms=7,
            attempts=1,
        )


# ---------------------------------------------------------------------------
# Embeddings (M6)
# ---------------------------------------------------------------------------

import hashlib  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402

_WORDS = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic bag-of-words vectors: texts sharing words are close.

    Each word is hashed to one of 384 dimensions, so similarity reflects word
    overlap. That is enough to test ranking, filtering, and access control
    without loading a model; real semantic behaviour is tested separately
    against the actual model in ``test_embedding_model.py``.
    """

    model = "fake-embedder@000000000001"
    dimensions = 384
    max_tokens = 256

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.document_calls: list[int] = []
        self.query_calls = 0

    def count_tokens(self, texts: list[str]) -> list[int]:
        return [len(t.split()) for t in texts]

    def vector(self, text: str) -> list[float]:
        v = [0.0] * self.dimensions
        for word in _WORDS.findall(text.lower()):
            v[
                int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % self.dimensions
            ] += 1
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0:
            v[0], norm = 1.0, 1.0
        return [x / norm for x in v]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.error is not None:
            raise self.error
        self.document_calls.append(len(texts))
        return [self.vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        if self.error is not None:
            raise self.error
        self.query_calls += 1
        return self.vector(text)

    def health_check(self) -> tuple[bool, str]:
        return True, "fake embedder"
