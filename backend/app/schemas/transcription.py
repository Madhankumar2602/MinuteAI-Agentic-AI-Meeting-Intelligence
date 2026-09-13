"""The contract between MinuteAI and the transcription model."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TranscriptSegment(BaseModel):
    speaker: str = Field(
        description=(
            "The speaker's name when the conversation makes it clear (they introduce "
            "themselves, or are addressed by name and reply). Otherwise 'Speaker 1', "
            "'Speaker 2', ... used consistently for the same voice."
        )
    )
    start_seconds: float | None = Field(
        description="Approximate start time of the segment in seconds from the beginning."
    )
    text: str = Field(description="Exactly what was said, verbatim.")


class TranscriptionResult(BaseModel):
    language: str | None = Field(description="ISO 639-1 code of the main language, e.g. 'en'.")
    segments: list[TranscriptSegment]
