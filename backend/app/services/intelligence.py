"""Meeting intelligence pipeline: transcript -> LLM -> validated records.

    transcript ─► prompt ─► LLMProvider.generate_structured(MeetingExtraction)
                                   │  (schema-validated JSON)
                                   ▼
                        normalise_extraction()   deterministic Python:
                                   │             trim, dedupe, parse dates,
                                   │             match owners, verify evidence
                                   ▼
                        persist in ONE transaction
                        summaries / decisions / action_items / participants

The LLM is used for what only it can do (reading language). Everything that
can be computed reliably (date parsing, owner matching, grounding checks,
status bookkeeping) is ordinary Python, so it is deterministic and unit-testable
without calling the model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.core.logging import get_logger
from app.db.models import (
    ActionItem,
    ActionItemPriority,
    Decision,
    Meeting,
    MeetingParticipant,
    MeetingStatus,
    MeetingSummary,
    Transcript,
    participant_name_key,
)
from app.schemas.extraction import MeetingExtraction
from app.services.grounding import TranscriptIndex
from app.services.llm.base import LLMError, LLMProvider
from app.services.prompts import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SYSTEM_INSTRUCTION,
    build_extraction_prompt,
)

logger = get_logger(__name__)

# Column limits, mirrored from the models so over-long model output is trimmed
# rather than causing a database error that would discard the whole run.
_NAME_MAX = 255
_DEADLINE_TEXT_MAX = 255

# How far a resolved deadline may sit from the meeting date before it is
# treated as a mis-resolution (e.g. the model picking the wrong year).
_MAX_DEADLINE_DAYS_BEFORE_MEETING = 30
_MAX_DEADLINE_DAYS_AFTER_MEETING = 3 * 365


# ---------------------------------------------------------------------------
# Normalisation: pure functions, no I/O
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NormalisedDecision:
    decision_text: str
    context: str | None
    evidence_quote: str | None
    evidence_verified: bool


@dataclass(slots=True)
class NormalisedActionItem:
    task: str
    owner_name: str | None
    deadline: date | None
    deadline_text: str | None
    priority: ActionItemPriority | None
    evidence_quote: str | None
    evidence_verified: bool


@dataclass(slots=True)
class NormalisedExtraction:
    summary: str
    key_points: list[str]
    participants: list[str]  # display names, de-duplicated, extraction order
    decisions: list[NormalisedDecision]
    action_items: list[NormalisedActionItem]
    warnings: list[str] = field(default_factory=list)


def _clean(value: str | None, limit: int | None = None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if limit is not None and len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip()
    return cleaned


def parse_deadline(raw: str | None, meeting_date: date, warnings: list[str]) -> date | None:
    """Accept only a real ISO date within a plausible window of the meeting."""
    raw = _clean(raw)
    if raw is None:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        warnings.append(f"discarded unparseable deadline {raw!r}")
        return None

    delta = (parsed - meeting_date).days
    if delta < -_MAX_DEADLINE_DAYS_BEFORE_MEETING or delta > _MAX_DEADLINE_DAYS_AFTER_MEETING:
        warnings.append(f"discarded implausible deadline {raw} for meeting on {meeting_date}")
        return None
    return parsed


def normalise_extraction(
    extraction: MeetingExtraction, *, transcript: str, meeting_date: date
) -> NormalisedExtraction:
    index = TranscriptIndex(transcript)
    warnings: list[str] = []

    decisions = [
        NormalisedDecision(
            decision_text=text,
            context=_clean(d.context),
            evidence_quote=_clean(d.evidence_quote),
            evidence_verified=index.supports(d.evidence_quote),
        )
        for d in extraction.decisions
        if (text := _clean(d.decision))
    ]

    action_items: list[NormalisedActionItem] = []
    for a in extraction.action_items:
        task = _clean(a.task)
        if not task:
            continue
        deadline_text = _clean(a.deadline_text, _DEADLINE_TEXT_MAX)
        action_items.append(
            NormalisedActionItem(
                task=task,
                owner_name=_clean(a.owner, _NAME_MAX),
                deadline=parse_deadline(a.deadline, meeting_date, warnings),
                deadline_text=deadline_text,
                priority=ActionItemPriority(a.priority) if a.priority else None,
                evidence_quote=_clean(a.evidence_quote),
                evidence_verified=index.supports(a.evidence_quote),
            )
        )

    # Participants: everyone the model listed, plus any action-item owner it
    # forgot to list, de-duplicated case-insensitively in first-seen order.
    seen: dict[str, str] = {}
    for name in [*extraction.participants, *(a.owner_name for a in action_items)]:
        cleaned = _clean(name, _NAME_MAX)
        if cleaned and participant_name_key(cleaned) not in seen:
            seen[participant_name_key(cleaned)] = cleaned

    unverified = sum(not d.evidence_verified for d in decisions) + sum(
        not a.evidence_verified for a in action_items
    )
    if unverified:
        warnings.append(f"{unverified} item(s) cite evidence not found in the transcript")

    return NormalisedExtraction(
        summary=_clean(extraction.summary) or "",
        key_points=[p for kp in extraction.key_points if (p := _clean(kp))],
        participants=list(seen.values()),
        decisions=decisions,
        action_items=action_items,
        warnings=warnings,
    )


def match_owner(owner_name: str | None, participants: dict[str, uuid.UUID]) -> uuid.UUID | None:
    """Link an owner name to a participant id.

    1. Exact normalised match ("priya sharma" == "Priya Sharma").
    2. Otherwise a one-word owner matching the first name of exactly ONE
       participant ("Priya" -> "Priya Sharma"). If two participants share that
       first name the owner is left unlinked: guessing would silently assign a
       task to the wrong person.
    """
    if not owner_name:
        return None
    key = participant_name_key(owner_name)
    if key in participants:
        return participants[key]
    if " " not in key:
        candidates = [pid for pkey, pid in participants.items() if pkey.split(" ")[0] == key]
        if len(candidates) == 1:
            return candidates[0]
    return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProcessingOutcome:
    meeting_id: uuid.UUID
    cached: bool
    warnings: list[str]


async def process_meeting(
    db: AsyncSession,
    *,
    meeting: Meeting,
    llm: LLMProvider,
    force: bool = False,
) -> ProcessingOutcome:
    """Run extraction for an already-authorised meeting.

    Idempotent: when the transcript, prompt version, and model are unchanged
    since the last successful run, the stored result is returned without
    calling the LLM, unless ``force`` is set.
    """
    meeting_id = meeting.id  # captured: ORM state is expired after a rollback

    transcript = await db.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
    if transcript is None:
        raise ConflictError(
            "Add a transcript before processing this meeting.", code="transcript_missing"
        )

    if meeting.status == MeetingStatus.PROCESSING and not force:
        raise ConflictError(
            "This meeting is already being processed.", code="processing_in_progress"
        )

    existing = await db.scalar(
        select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id)
    )
    if (
        not force
        and existing is not None
        and meeting.status == MeetingStatus.COMPLETED
        and existing.transcript_sha256 == transcript.content_sha256
        and existing.prompt_version == EXTRACTION_PROMPT_VERSION
        and existing.model == llm.model
    ):
        logger.info(
            "processing skipped, cached result is current", extra={"meeting_id": str(meeting_id)}
        )
        return ProcessingOutcome(meeting_id=meeting_id, cached=True, warnings=[])

    # Read everything the LLM call needs BEFORE committing the status change.
    title, meeting_date = meeting.title, meeting.meeting_date
    content, content_sha = transcript.content, transcript.content_sha256

    # Commit PROCESSING first. This releases the pooled DB connection for the
    # duration of the LLM call (which can take tens of seconds), and makes the
    # in-progress state visible to other requests.
    meeting.status = MeetingStatus.PROCESSING
    await db.commit()
    logger.info("processing started", extra={"meeting_id": str(meeting_id), "force": force})

    try:
        result = await llm.generate_structured(
            system_instruction=EXTRACTION_SYSTEM_INSTRUCTION,
            prompt=build_extraction_prompt(
                title=title, meeting_date=meeting_date, transcript=content
            ),
            schema=MeetingExtraction,
        )
        normalised = normalise_extraction(
            result.data, transcript=content, meeting_date=meeting_date.date()
        )
        await _replace_results(db, meeting_id=meeting_id, normalised=normalised)
        db.add(
            MeetingSummary(
                meeting_id=meeting_id,
                summary_text=normalised.summary,
                key_points=normalised.key_points,
                provider=result.provider,
                model=result.model,
                prompt_version=EXTRACTION_PROMPT_VERSION,
                transcript_sha256=content_sha,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                latency_ms=result.latency_ms,
            )
        )
        await db.execute(
            update(Meeting).where(Meeting.id == meeting_id).values(status=MeetingStatus.COMPLETED)
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        # A Core UPDATE rather than touching the ORM object: after rollback the
        # instance is expired, and reading it in async code would raise.
        await db.execute(
            update(Meeting).where(Meeting.id == meeting_id).values(status=MeetingStatus.FAILED)
        )
        await db.commit()
        logger.warning(
            "processing failed",
            extra={
                "meeting_id": str(meeting_id),
                "error": type(exc).__name__,
                "llm_error": isinstance(exc, LLMError),
            },
        )
        raise

    logger.info(
        "processing completed",
        extra={
            "meeting_id": str(meeting_id),
            "decisions": len(normalised.decisions),
            "action_items": len(normalised.action_items),
            "participants": len(normalised.participants),
            "warnings": len(normalised.warnings),
        },
    )
    return ProcessingOutcome(meeting_id=meeting_id, cached=False, warnings=normalised.warnings)


async def _replace_results(
    db: AsyncSession, *, meeting_id: uuid.UUID, normalised: NormalisedExtraction
) -> None:
    """Swap previous AI output for the new output inside the caller's transaction.

    Replacement, not merge: re-processing re-derives everything from the
    transcript. The consequence, documented as a limitation, is that manual
    status changes on previously extracted action items are reset. The API
    therefore requires an explicit ``force=true`` to re-run on an unchanged
    transcript.
    """
    for model in (ActionItem, Decision, MeetingSummary, MeetingParticipant):
        await db.execute(delete(model).where(model.meeting_id == meeting_id))

    participant_ids: dict[str, uuid.UUID] = {}
    for name in normalised.participants:
        participant = MeetingParticipant(
            id=uuid.uuid4(),
            meeting_id=meeting_id,
            display_name=name,
            name_key=participant_name_key(name),
        )
        db.add(participant)
        participant_ids[participant.name_key] = participant.id
    # Flush so participant rows exist before action items reference them.
    await db.flush()

    for position, d in enumerate(normalised.decisions):
        db.add(
            Decision(
                meeting_id=meeting_id,
                position=position,
                decision_text=d.decision_text,
                context=d.context,
                evidence_quote=d.evidence_quote,
                evidence_verified=d.evidence_verified,
            )
        )

    for position, a in enumerate(normalised.action_items):
        db.add(
            ActionItem(
                meeting_id=meeting_id,
                position=position,
                task=a.task,
                owner_name=a.owner_name,
                owner_participant_id=match_owner(a.owner_name, participant_ids),
                deadline=a.deadline,
                deadline_text=a.deadline_text,
                priority=a.priority,
                evidence_quote=a.evidence_quote,
                evidence_verified=a.evidence_verified,
            )
        )
