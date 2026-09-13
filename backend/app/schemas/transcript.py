"""Transcript request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.db.models.transcript import TranscriptSource

MIN_TRANSCRIPT_CHARS = 20


class TranscriptUpsertRequest(BaseModel):
    content: str = Field(
        min_length=MIN_TRANSCRIPT_CHARS,
        max_length=settings.transcript_max_chars,
        description="Plain-text transcript. Speaker labels such as 'Priya:' improve owner extraction.",
    )
    language: str | None = Field(default=None, max_length=16, examples=["en"])

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        # PostgreSQL TEXT cannot store NUL bytes. Without this check a pasted
        # binary file reaches the database and surfaces as a 500 instead of a 422.
        if "\x00" in v:
            raise ValueError("content must not contain NUL characters")
        # Normalise line endings so the content hash does not change when the
        # same text is pasted from Windows and from macOS.
        v = v.replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(v) < MIN_TRANSCRIPT_CHARS:
            raise ValueError(
                f"content must be at least {MIN_TRANSCRIPT_CHARS} non-blank characters"
            )
        return v


class TranscriptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    meeting_id: uuid.UUID
    content: str
    content_sha256: str
    char_count: int
    word_count: int
    language: str | None
    source: TranscriptSource
    created_at: datetime
    updated_at: datetime
