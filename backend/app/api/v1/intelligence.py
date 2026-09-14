"""Transcript input, AI processing, and extracted results for one meeting.

Every route resolves the meeting through ``authorize_meeting_access`` first.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
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
from app.schemas.jobs import JobResponse, ProcessSubmissionResponse
from app.schemas.transcript import TranscriptResponse, TranscriptUpsertRequest
from app.services.authorization import AccessLevel, authorize_meeting_access
from app.services.embeddings import EmbeddingProvider, get_embedder
from app.services.embeddings.indexing import is_index_current
from app.services.intelligence import is_result_current, require_transcript
from app.services.job_store import JobStore, get_job_store
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider
from app.services.transcription import media_needing_transcription

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
    if meeting.status in (MeetingStatus.QUEUED, MeetingStatus.PROCESSING):
        raise ConflictError(
            "The transcript cannot change while the meeting is queued or being processed.",
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
    transcript.source = (
        TranscriptSource.NOTES if payload.kind == "notes" else TranscriptSource.MANUAL
    )
    # A typed transcript replaces any transcribed one entirely, including its
    # provenance; otherwise it would look as if it came from the recording.
    transcript.media_id = None
    transcript.media_etag = None
    transcript.raw_s3_key = None
    transcript.transcription_model = None
    transcript.duration_seconds = None

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
    response_model=ProcessSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue AI extraction of summary, decisions, and action items",
    responses={200: {"description": "Stored results are already current; nothing was queued."}},
)
async def process(
    meeting_id: uuid.UUID,
    request: Request,
    response: Response,
    db: DbSession,
    current_user: CurrentUser,
    llm: LLM,
    store: Annotated[JobStore, Depends(get_job_store)],
    embedder: Annotated[EmbeddingProvider, Depends(get_embedder)],
    force: Annotated[
        bool,
        Query(
            description=(
                "Re-run even if the stored result is current. Replaces previous "
                "results, including manual status changes on action items."
            )
        ),
    ] = False,
) -> ProcessSubmissionResponse:
    """Returns immediately. Poll ``GET /api/v1/jobs/{job_id}`` for the outcome.

    Submitting again while a job for this meeting is still queued or running
    returns that same job rather than starting a duplicate.
    """
    meeting = await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.WRITE)
    # Validate synchronously what can be validated synchronously: a request
    # that can only fail should get a 409 now, not a job that fails later.
    pending_recording = await media_needing_transcription(db, meeting_id)
    if pending_recording is None:
        await require_transcript(db, meeting_id)

    # While a job is active, always defer to it (create_job returns it) rather
    # than answering "cached" for a meeting that is visibly queued or running.
    active = meeting.status in (MeetingStatus.QUEUED, MeetingStatus.PROCESSING)
    if (
        not force
        and not active
        and pending_recording is None
        and await is_result_current(db, meeting, model=llm.model)
        and await is_index_current(db, meeting_id, embedder=embedder)
    ):
        if meeting.status != MeetingStatus.COMPLETED:
            # e.g. a forced re-run failed but the earlier results are still current.
            meeting.status = MeetingStatus.COMPLETED
            await db.commit()
        response.status_code = status.HTTP_200_OK
        return ProcessSubmissionResponse(cached=True, meeting_status=meeting.status, job=None)

    job, created = await store.create_job(
        meeting_id=meeting_id, owner_id=current_user.id, force=force
    )
    if created:
        meeting.status = MeetingStatus.QUEUED
        await db.commit()
        worker = getattr(request.app.state, "worker", None)
        if worker is not None:
            worker.notify()

    logger.info(
        "processing submitted",
        extra={"meeting_id": str(meeting_id), "job_id": job.job_id, "job_created": created},
    )
    return ProcessSubmissionResponse(
        cached=False, meeting_status=meeting.status, job=JobResponse.from_record(job)
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
