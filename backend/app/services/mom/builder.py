"""Assemble the Minutes of Meeting from stored records, and validate them.

    meeting · transcript · recording · summary (+ MOM fields) · participants
    decisions · action items (with the user's current statuses)
                              │
                              ▼
               MinutesOfMeeting  ──►  review flags (deterministic)

The review step is the "validation & enrichment" stage of the core workflow.
It is ordinary code, not a model: it checks what a careful minute-taker would
check before circulating minutes (every task has an owner and a date, quoted
evidence was found in the input, speakers are named). It is rule-based
validation, not a model, so its results are predictable and testable.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.db.models import (
    OPEN_ACTION_STATUSES,
    ActionItem,
    Decision,
    Meeting,
    MeetingMedia,
    MeetingParticipant,
    MeetingSummary,
    Transcript,
    TranscriptSource,
)
from app.schemas.mom import (
    MinutesOfMeeting,
    MomActionItem,
    MomDecision,
    MomPendingItem,
    MomReviewFlag,
    MomSource,
    MomSpeaker,
)

# "Speaker 1", "Speaker 2": the transcription model's label for a voice it
# could not name (schemas/transcription.py).
_UNNAMED_SPEAKER = re.compile(r"^speaker\s*\d+$", re.IGNORECASE)


class MinutesNotReadyError(ConflictError):
    code = "minutes_not_ready"
    message = "Minutes are available once the meeting has been processed."


async def build_minutes(db: AsyncSession, meeting_id: uuid.UUID) -> MinutesOfMeeting:
    """The minutes for a meeting the caller has already been authorised to read."""
    meeting = await db.scalar(select(Meeting).where(Meeting.id == meeting_id))
    summary = await db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
    transcript = await db.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
    if meeting is None or summary is None or transcript is None:
        raise MinutesNotReadyError()

    media = await db.scalar(select(MeetingMedia).where(MeetingMedia.meeting_id == meeting_id))
    participants = (
        await db.scalars(
            select(MeetingParticipant.display_name).where(
                MeetingParticipant.meeting_id == meeting_id
            )
        )
    ).all()
    decision_rows = (
        await db.scalars(
            select(Decision).where(Decision.meeting_id == meeting_id).order_by(Decision.position)
        )
    ).all()
    action_rows = (
        await db.scalars(
            select(ActionItem)
            .where(ActionItem.meeting_id == meeting_id)
            .order_by(ActionItem.position)
        )
    ).all()

    decisions = [
        MomDecision(
            number=i,
            text=d.decision_text,
            context=d.context,
            status=d.status,
            evidence_quote=d.evidence_quote,
            evidence_verified=d.evidence_verified,
        )
        for i, d in enumerate(decision_rows, start=1)
    ]
    action_items = [
        MomActionItem(
            number=i,
            task=a.task,
            owner=a.owner_name,
            deadline=a.deadline,
            deadline_text=a.deadline_text,
            priority=a.priority,
            status=a.status,
            evidence_quote=a.evidence_quote,
            evidence_verified=a.evidence_verified,
        )
        for i, a in enumerate(action_rows, start=1)
    ]
    pending = [MomPendingItem.model_validate(u) for u in summary.unresolved_items]

    total_words = sum(s.get("words", 0) for s in summary.speakers) or 0
    speakers = [
        MomSpeaker(
            name=s["name"],
            contribution=s.get("contribution"),
            turns=s.get("turns", 0),
            words=s.get("words", 0),
            share=round(s.get("words", 0) / total_words, 3) if total_words else 0.0,
        )
        for s in summary.speakers
    ]

    # Participants in the order people spoke, then anyone else named.
    ordered = [s.name for s in speakers]
    ordered += sorted(
        (p for p in participants if p.casefold() not in {n.casefold() for n in ordered}),
        key=str.casefold,
    )

    next_steps, derived = list(summary.next_steps), False
    if not next_steps:
        derived = True
        next_steps = [_describe_task(a) for a in action_items if a.status in OPEN_ACTION_STATUSES]

    with_evidence = [*decisions, *action_items, *pending]
    is_stale = transcript.content_sha256 != summary.transcript_sha256
    minutes = MinutesOfMeeting(
        meeting_id=meeting.id,
        title=meeting.title,
        agenda=meeting.description,
        meeting_date=meeting.meeting_date,
        participants=ordered,
        speakers=speakers,
        executive_summary=summary.summary_text,
        key_points=list(summary.key_points),
        keywords=list(summary.keywords),
        decisions=decisions,
        action_items=action_items,
        pending_items=pending,
        next_steps=next_steps,
        next_steps_derived=derived,
        review_flags=[],
        source=MomSource(
            input_kind=_input_kind(transcript),
            transcript_words=transcript.word_count,
            transcript_sha256=transcript.content_sha256,
            language=transcript.language,
            recording_filename=media.original_filename if media else None,
            recording_type=media.content_type if media else None,
            duration_seconds=transcript.duration_seconds,
            transcription_model=transcript.transcription_model,
            extraction_provider=summary.provider,
            extraction_model=summary.model,
            prompt_version=summary.prompt_version,
            extracted_at=summary.created_at,
            evidence_verified=sum(item.evidence_verified for item in with_evidence),
            evidence_total=len(with_evidence),
        ),
        is_stale=is_stale,
    )
    minutes.review_flags = review_minutes(minutes)
    return minutes


def _input_kind(transcript: Transcript) -> str:
    if transcript.source == TranscriptSource.TRANSCRIPTION:
        return "recording"
    return "notes" if transcript.source == TranscriptSource.NOTES else "transcript"


def _describe_task(item: MomActionItem) -> str:
    parts = [item.task]
    if item.owner:
        parts.append(item.owner)
    if item.deadline:
        parts.append(f"by {item.deadline.isoformat()}")
    return " — ".join(parts)


def review_minutes(minutes: MinutesOfMeeting) -> list[MomReviewFlag]:
    """Checks a person should make before circulating the minutes. Pure function."""
    flags: list[MomReviewFlag] = []
    open_items = [a for a in minutes.action_items if a.status in OPEN_ACTION_STATUSES]

    if minutes.is_stale:
        flags.append(
            MomReviewFlag(
                kind="stale_transcript",
                message="The transcript changed after these minutes were produced. Re-run the analysis.",
            )
        )
    for a in open_items:
        if not a.owner:
            flags.append(
                MomReviewFlag(
                    kind="missing_owner", message=f"Action {a.number} has no owner: {a.task}"
                )
            )
        if a.deadline is None and a.deadline_text:
            flags.append(
                MomReviewFlag(
                    kind="unresolved_deadline",
                    message=(
                        f'Action {a.number}: "{a.deadline_text}" could not be turned into a date.'
                    ),
                )
            )
        elif a.deadline is None:
            flags.append(
                MomReviewFlag(
                    kind="missing_deadline", message=f"Action {a.number} has no deadline: {a.task}"
                )
            )
    unverified = [
        *(f"decision {d.number}" for d in minutes.decisions if not d.evidence_verified),
        *(f"action {a.number}" for a in minutes.action_items if not a.evidence_verified),
        *(
            f"pending item {i}"
            for i, p in enumerate(minutes.pending_items, start=1)
            if not p.evidence_verified
        ),
    ]
    if unverified:
        flags.append(
            MomReviewFlag(
                kind="unverified_evidence",
                message="No supporting quote was found in the input for "
                + ", ".join(unverified)
                + ". Check these against the transcript.",
            )
        )
    unnamed = [s.name for s in minutes.speakers if _UNNAMED_SPEAKER.match(s.name)]
    if unnamed:
        flags.append(
            MomReviewFlag(
                kind="unnamed_speakers",
                message=f"{len(unnamed)} speaker(s) could not be identified by name: "
                + ", ".join(unnamed)
                + ".",
            )
        )
    return flags
