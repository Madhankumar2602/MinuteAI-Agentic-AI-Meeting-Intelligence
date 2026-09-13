"""Transcript input, AI processing, and extracted results for one meeting.

Every route resolves the meeting through ``authorize_meeting_access`` first.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.db.models import (
    ActionItem,
    Decision,
    Meeting,
    MeetingParticipant,
    MeetingStatus,
    MeetingSummary,
    Transcript,
    TranscriptSource,
)
from app.schemas.intelligence import (
    ActionItemResponse,
    DecisionResponse,
    MeetingIntelligenceResponse,
    ParticipantResponse,
    SummaryResponse,
)
from app.schemas.transcript import TranscriptResponse, TranscriptUpsertRequest
from app.services.authorization import AccessLevel, authorize_meeting_access
from app.services.intelligence import process_meeting
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider

logger = get_logger(__name__)

router = APIRouter(prefix="/meetings/{meeting_id}", tags=["meeting intelligence"])

LLM = Annotated[LLMProvider, Depends(get_llm_provider)]


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------


@router.put(
    "/transcript",
    response_model=TranscriptResponse,
    summary="Add or replace the meeting transcript",
)
async def upsert_transcript(
    meeting_id: uuid.UUID,
    payload: TranscriptUpsertRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> Transcript:
    """PUT, not POST: a meeting has exactly one transcript, and sending the same
    body twice leaves the same state (idempotent)."""
    meeting = await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.WRITE)
    if meeting.status == MeetingStatus.PROCESSING:
        raise ConflictError(
            "The transcript cannot change while the meeting is being processed.",
            code="processing_in_progress",
        )

    digest = hashlib.sha256(payload.content.encode("utf-8")).hexdigest()
    transcript = await db.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
    if transcript is None:
        transcript = Transcript(meeting_id=meeting_id)
        db.add(transcript)

    transcript.content = payload.content
    transcript.content_sha256 = digest
    transcript.char_count = len(payload.content)
    transcript.word_count = len(payload.content.split())
    transcript.language = payload.language
    transcript.source = TranscriptSource.MANUAL

    await db.commit()
    await db.refresh(transcript)
    # Metadata only. Transcript text is sensitive and never logged.
    logger.info(
        "transcript saved",
        extra={
            "meeting_id": str(meeting_id),
            "chars": transcript.char_count,
            "sha256": digest[:12],
        },
    )
    return transcript


@router.get("/transcript", response_model=TranscriptResponse, summary="Get the meeting transcript")
async def get_transcript(
    meeting_id: uuid.UUID, db: DbSession, current_user: CurrentUser
) -> Transcript:
    await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)
    transcript = await db.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
    if transcript is None:
        raise NotFoundError("This meeting has no transcript yet.")
    return transcript


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


@router.post(
    "/process",
    response_model=MeetingIntelligenceResponse,
    status_code=status.HTTP_200_OK,
    summary="Extract summary, decisions, and action items with the LLM",
)
async def process(
    meeting_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    llm: LLM,
    force: Annotated[
        bool,
        Query(
            description=(
                "Re-run even if the stored result is current. Replaces previous "
                "results, including manual status changes on action items."
            )
        ),
    ] = False,
) -> MeetingIntelligenceResponse:
    """Synchronous in M2 - the request waits for the LLM (typically 5-30 s).

    M3 turns this into a queued background job that returns 202 immediately.
    """
    meeting = await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.WRITE)
    outcome = await process_meeting(db, meeting=meeting, llm=llm, force=force)
    return await _load_intelligence(
        db, meeting_id, cached=outcome.cached, warnings=outcome.warnings
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@router.get(
    "/intelligence",
    response_model=MeetingIntelligenceResponse,
    summary="Summary, participants, decisions, and action items in one response",
)
async def get_intelligence(
    meeting_id: uuid.UUID, db: DbSession, current_user: CurrentUser
) -> MeetingIntelligenceResponse:
    await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)
    return await _load_intelligence(db, meeting_id, cached=True, warnings=[])


@router.get("/summary", response_model=SummaryResponse, summary="Get the AI summary")
async def get_summary(
    meeting_id: uuid.UUID, db: DbSession, current_user: CurrentUser
) -> SummaryResponse:
    await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)
    summary = await _summary_response(db, meeting_id)
    if summary is None:
        raise NotFoundError("This meeting has not been processed yet.")
    return summary


@router.get(
    "/decisions", response_model=list[DecisionResponse], summary="Decisions from this meeting"
)
async def list_meeting_decisions(
    meeting_id: uuid.UUID, db: DbSession, current_user: CurrentUser
) -> list[Decision]:
    await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)
    rows = await db.scalars(
        select(Decision).where(Decision.meeting_id == meeting_id).order_by(Decision.position)
    )
    return list(rows.all())


@router.get(
    "/action-items",
    response_model=list[ActionItemResponse],
    summary="Action items from this meeting",
)
async def list_meeting_action_items(
    meeting_id: uuid.UUID, db: DbSession, current_user: CurrentUser
) -> list[ActionItem]:
    await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)
    rows = await db.scalars(
        select(ActionItem).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.position)
    )
    return list(rows.all())


@router.get(
    "/participants",
    response_model=list[ParticipantResponse],
    summary="People named in this meeting",
)
async def list_participants(
    meeting_id: uuid.UUID, db: DbSession, current_user: CurrentUser
) -> list[MeetingParticipant]:
    await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)
    rows = await db.scalars(
        select(MeetingParticipant)
        .where(MeetingParticipant.meeting_id == meeting_id)
        .order_by(MeetingParticipant.display_name)
    )
    return list(rows.all())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _summary_response(db: DbSession, meeting_id: uuid.UUID) -> SummaryResponse | None:
    summary = await db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
    if summary is None:
        return None
    current_sha = await db.scalar(
        select(Transcript.content_sha256).where(Transcript.meeting_id == meeting_id)
    )
    response = SummaryResponse.model_validate(summary)
    response.is_stale = current_sha is not None and current_sha != summary.transcript_sha256
    return response


async def _load_intelligence(
    db: DbSession, meeting_id: uuid.UUID, *, cached: bool, warnings: list[str]
) -> MeetingIntelligenceResponse:
    meeting_status = await db.scalar(select(Meeting.status).where(Meeting.id == meeting_id))
    participants = await db.scalars(
        select(MeetingParticipant)
        .where(MeetingParticipant.meeting_id == meeting_id)
        .order_by(MeetingParticipant.display_name)
    )
    decisions = await db.scalars(
        select(Decision).where(Decision.meeting_id == meeting_id).order_by(Decision.position)
    )
    action_items = await db.scalars(
        select(ActionItem).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.position)
    )
    return MeetingIntelligenceResponse(
        meeting_id=meeting_id,
        status=meeting_status,
        cached=cached,
        warnings=warnings,
        summary=await _summary_response(db, meeting_id),
        participants=[ParticipantResponse.model_validate(p) for p in participants.all()],
        decisions=[DecisionResponse.model_validate(d) for d in decisions.all()],
        action_items=[ActionItemResponse.model_validate(a) for a in action_items.all()],
    )
