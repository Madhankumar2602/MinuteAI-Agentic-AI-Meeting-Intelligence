"""The Minutes of Meeting (MOM) data model: the product's central output (M7).

One model feeds the API (``GET /meetings/{id}/mom``), the web view, and the PDF,
so all three always show the same minutes. It is assembled from stored records
by ``app.services.mom.builder``, never generated separately, so a correction a
user makes (an action item marked done, a decision resolved) appears in the
next rendering of the minutes.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import ActionItemPriority, ActionItemStatus, DecisionStatus


class MomSpeaker(BaseModel):
    name: str
    contribution: str | None
    turns: int = Field(description="Speaking turns counted from the transcript (0 for notes).")
    words: int
    share: float = Field(description="Share of all spoken words, 0 to 1.")


class MomDecision(BaseModel):
    number: int
    text: str
    context: str | None
    status: DecisionStatus
    evidence_quote: str | None
    evidence_verified: bool


class MomActionItem(BaseModel):
    number: int
    task: str
    owner: str | None
    deadline: date | None
    deadline_text: str | None
    priority: ActionItemPriority | None
    status: ActionItemStatus
    evidence_quote: str | None
    evidence_verified: bool


class MomPendingItem(BaseModel):
    item: str
    evidence_quote: str | None
    evidence_verified: bool


ReviewKind = Literal[
    "missing_owner",
    "missing_deadline",
    "unresolved_deadline",
    "unverified_evidence",
    "unnamed_speakers",
    "stale_transcript",
]


class MomReviewFlag(BaseModel):
    """Something a person should check before relying on the minutes.

    Produced by deterministic validation, not by the model.
    """

    kind: ReviewKind
    message: str


class MomSource(BaseModel):
    input_kind: Literal["transcript", "notes", "recording"]
    transcript_words: int
    transcript_sha256: str
    language: str | None
    recording_filename: str | None
    recording_type: str | None
    duration_seconds: int | None
    transcription_model: str | None
    extraction_provider: str
    extraction_model: str
    prompt_version: str
    extracted_at: datetime
    evidence_verified: int = Field(description="Items whose evidence quote was found in the input.")
    evidence_total: int = Field(description="Items that carry an evidence quote requirement.")


class MinutesOfMeeting(BaseModel):
    meeting_id: uuid.UUID
    title: str
    agenda: str | None = Field(description="The meeting description / agenda, as entered.")
    meeting_date: datetime
    participants: list[str]
    speakers: list[MomSpeaker]
    executive_summary: str
    key_points: list[str]
    keywords: list[str]
    decisions: list[MomDecision]
    action_items: list[MomActionItem]
    pending_items: list[MomPendingItem]
    next_steps: list[str]
    next_steps_derived: bool = Field(
        description="True when no next steps were stated and they were derived from open action items."
    )
    review_flags: list[MomReviewFlag]
    source: MomSource
    is_stale: bool = Field(description="The transcript changed after these minutes were extracted.")


class MomPdfResponse(BaseModel):
    filename: str
    size_bytes: int
    pages: int | None = Field(description="Known when this request rendered the document.")
    fingerprint: str = Field(description="Hash of the minutes content the PDF was rendered from.")
    reused: bool = Field(description="True when an identical stored PDF was reused.")
    view_url: str = Field(description="Opens the PDF in the browser. Short-lived.")
    download_url: str = Field(description="Downloads the PDF. Short-lived.")
    expires_in: int
