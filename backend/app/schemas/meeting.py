"""Meeting request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.meeting import MeetingSourceType, MeetingStatus


class MeetingCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    meeting_date: datetime
    description: str | None = Field(default=None, max_length=5000)
    source_type: MeetingSourceType = MeetingSourceType.TEXT

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("title must not be blank")
        return stripped


class MeetingUpdateRequest(BaseModel):
    """Partial update - every field optional.

    Distinguishing "field absent" from "field set to null" is done with
    ``model_dump(exclude_unset=True)`` in the route, so PATCH with an empty
    body is a no-op rather than wiping the record.
    """

    title: str | None = Field(default=None, min_length=1, max_length=255)
    meeting_date: datetime | None = None
    description: str | None = Field(default=None, max_length=5000)
    source_type: MeetingSourceType | None = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("title must not be blank")
        return stripped


class MeetingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    description: str | None
    meeting_date: datetime
    source_type: MeetingSourceType
    status: MeetingStatus
    created_at: datetime
    updated_at: datetime
