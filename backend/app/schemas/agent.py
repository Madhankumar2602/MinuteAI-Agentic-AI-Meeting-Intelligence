"""Follow-up agent API schemas (M9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import (
    AgentRunStatus,
    DraftSource,
    ProposalKind,
    ProposalPriority,
    ProposalStatus,
)
from app.schemas.ask import AskSource


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trigger: str
    status: AgentRunStatus
    started_at: datetime
    finished_at: datetime | None
    model: str | None = Field(description="The drafting model; null if it was not called.")
    prompt_version: str
    candidates_found: int
    proposals_created: int
    used_fallback: bool = Field(description="True when drafts came from templates.")
    steps: list[dict[str, Any]] = Field(description="What the agent did, step by step.")
    error_code: str | None


class ProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID | None
    meeting_id: uuid.UUID
    meeting_title: str
    meeting_date: datetime
    action_item_id: uuid.UUID | None
    decision_id: uuid.UUID | None
    kind: ProposalKind
    priority: ProposalPriority
    title: str
    rationale: str
    recipients: list[str]
    draft_subject: str
    draft_body: str
    drafted_by: DraftSource
    sources: list[AskSource]
    status: ProposalStatus
    final_subject: str | None
    final_body: str | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime


class ProposalList(BaseModel):
    items: list[ProposalResponse]
    pending: int = Field(description="Proposals waiting for a decision.")


class ApproveRequest(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=5000)
    note: str | None = Field(default=None, max_length=1000)


class RejectRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
