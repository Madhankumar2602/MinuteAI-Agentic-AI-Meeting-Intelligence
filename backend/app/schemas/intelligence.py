"""Response and update schemas for AI-extracted meeting intelligence."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.db.models import (
    OPEN_ACTION_STATUSES,
    ActionItemPriority,
    ActionItemStatus,
    DecisionStatus,
    MeetingStatus,
)
from app.schemas.common import PartialUpdate


class ParticipantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    email: str | None
    user_id: uuid.UUID | None


class SummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    summary_text: str
    key_points: list[str]
    provider: str
    model: str
    prompt_version: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    created_at: datetime
    is_stale: bool = Field(
        default=False,
        description="True when the transcript has changed since this summary was generated.",
    )


class DecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    meeting_id: uuid.UUID
    position: int
    decision_text: str
    context: str | None
    evidence_quote: str | None
    evidence_verified: bool
    status: DecisionStatus
    created_at: datetime
    updated_at: datetime


class ActionItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    meeting_id: uuid.UUID
    position: int
    task: str
    owner_name: str | None
    owner_participant_id: uuid.UUID | None
    deadline: date | None
    deadline_text: str | None
    priority: ActionItemPriority | None
    status: ActionItemStatus
    evidence_quote: str | None
    evidence_verified: bool
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_overdue(self) -> bool:
        # Dates are compared in UTC. Deadlines carry no time zone, so an item
        # due "today" becomes overdue at UTC midnight - a documented limitation.
        return (
            self.deadline is not None
            and self.status in OPEN_ACTION_STATUSES
            and self.deadline < datetime.now(UTC).date()
        )


class MeetingIntelligenceResponse(BaseModel):
    meeting_id: uuid.UUID
    status: MeetingStatus
    cached: bool = Field(
        description="True when the stored result was current and no LLM call was made."
    )
    warnings: list[str]
    summary: SummaryResponse | None
    participants: list[ParticipantResponse]
    decisions: list[DecisionResponse]
    action_items: list[ActionItemResponse]


class ActionItemUpdateRequest(PartialUpdate):
    """Human corrections to an extracted action item. Every field optional.

    ``deadline`` and ``priority`` may be set to null deliberately (to clear
    them); ``task`` and ``status`` may not.
    """

    NON_NULLABLE = frozenset({"task", "status"})

    task: str | None = Field(default=None, min_length=1, max_length=2000)
    status: ActionItemStatus | None = None
    deadline: date | None = None
    priority: ActionItemPriority | None = None


class DecisionUpdateRequest(PartialUpdate):
    NON_NULLABLE = frozenset({"status", "decision_text"})

    status: DecisionStatus | None = None
    decision_text: str | None = Field(default=None, min_length=1, max_length=2000)
