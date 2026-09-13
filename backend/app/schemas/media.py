"""Recording upload schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.jobs import JobResponse


class UploadUrlRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=1024, examples=["standup-2026-09-10.m4a"])
    content_type: str = Field(min_length=3, max_length=100, examples=["audio/mp4"])
    size_bytes: int = Field(
        gt=0, description="Checked early for a friendly error; storage enforces the real limit."
    )


class UploadUrlResponse(BaseModel):
    """Everything the browser needs to upload directly to storage.

    Send a multipart/form-data POST to ``upload_url`` containing every entry in
    ``fields`` followed by the file under the name ``file``. Then call
    ``POST /meetings/{id}/media/complete`` with ``upload_token``.
    """

    upload_url: str
    fields: dict[str, str]
    upload_token: str
    expires_in: int
    max_bytes: int


class UploadCompleteRequest(BaseModel):
    upload_token: str = Field(min_length=10)
    replace_manual_transcript: bool = Field(
        default=False,
        description=(
            "A typed/pasted transcript normally takes precedence over a recording. Set true "
            "to discard it and transcribe the recording instead."
        ),
    )


class MediaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    meeting_id: uuid.UUID
    content_type: str
    size_bytes: int
    original_filename: str | None
    created_at: datetime
    updated_at: datetime
    download_url: str | None = Field(
        default=None, description="Short-lived presigned URL for playback."
    )


class UploadCompleteResponse(BaseModel):
    media: MediaResponse
    job: JobResponse
