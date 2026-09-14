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

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
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
    TranscriptSource,
    participant_name_key,
)
from app.schemas.extraction import MeetingExtraction
from app.services.grounding import TranscriptIndex
from app.services.llm.base import LLMProvider
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

_MAX_KEYWORDS = 12
_KEYWORD_MAX = 60
# "Name: words" - the shape of pasted transcripts and of rendered transcriptions.
# Same pattern the web app uses to highlight speakers.
_SPEAKER_LINE = re.compile(r"^([^:\n]{1,40}):(.*)$")


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
class NormalisedSpeaker:
    name: str
    contribution: str | None
    # Counted from the transcript's speaker lines, not estimated by the model.
    turns: int
    words: int


@dataclass(slots=True)
class NormalisedOpenItem:
    item: str
    evidence_quote: str | None
    evidence_verified: bool


@dataclass(slots=True)
class NormalisedExtraction:
    summary: str
    key_points: list[str]
    participants: list[str]  # display names, de-duplicated, extraction order
    decisions: list[NormalisedDecision]
    action_items: list[NormalisedActionItem]
    keywords: list[str] = field(default_factory=list)
    speakers: list[NormalisedSpeaker] = field(default_factory=list)
    unresolved_items: list[NormalisedOpenItem] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
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


def speaker_statistics(transcript: str) -> dict[str, tuple[str, int, int]]:
    """name_key -> (display name, turns, words) from ``Name: words`` lines.

    Consecutive lines by the same speaker count as one turn.
    """
    stats: dict[str, list] = {}
    previous = None
    for line in transcript.splitlines():
        match = _SPEAKER_LINE.match(line.strip())
        if not match or not (name := _clean(match.group(1), _NAME_MAX)):
            continue
        key = participant_name_key(name)
        entry = stats.setdefault(key, [name, 0, 0])
        if key != previous:
            entry[1] += 1
        entry[2] += len(match.group(2).split())
        previous = key
    return {k: (v[0], v[1], v[2]) for k, v in stats.items()}


def _dedupe(values: list[str], limit: int | None = None, max_items: int | None = None) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        cleaned = _clean(value, limit)
        if cleaned and cleaned.casefold() not in seen:
            seen[cleaned.casefold()] = cleaned
    out = list(seen.values())
    return out[:max_items] if max_items is not None else out


def normalise_extraction(
    extraction: MeetingExtraction,
    *,
    transcript: str,
    meeting_date: date,
    input_kind: str = "transcript",
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

    unresolved = [
        NormalisedOpenItem(
            item=text,
            evidence_quote=_clean(u.evidence_quote),
            evidence_verified=index.supports(u.evidence_quote),
        )
        for u in extraction.unresolved_items
        if (text := _clean(u.item))
    ]

    # Speakers: the model's contribution summaries, joined to counts taken from
    # the transcript itself. Notes have no reliable speaker lines ("Agenda:" is
    # not a person), so their counts are zero and only named speakers appear.
    stats = speaker_statistics(transcript) if input_kind == "transcript" else {}
    speakers: dict[str, NormalisedSpeaker] = {}
    for s in extraction.speakers:
        name = _clean(s.name, _NAME_MAX)
        if not name or participant_name_key(name) in speakers:
            continue
        display, turns, words = stats.get(participant_name_key(name), (name, 0, 0))
        speakers[participant_name_key(name)] = NormalisedSpeaker(
            name=display, contribution=_clean(s.contribution), turns=turns, words=words
        )
    for key, (display, turns, words) in stats.items():
        if key not in speakers:  # spoke, but the model did not summarise them
            speakers[key] = NormalisedSpeaker(
                name=display, contribution=None, turns=turns, words=words
            )
    # Participants also include everyone who demonstrably spoke.
    for display, _, _ in stats.values():
        if participant_name_key(display) not in seen:
            seen[participant_name_key(display)] = display

    unverified = (
        sum(not d.evidence_verified for d in decisions)
        + sum(not a.evidence_verified for a in action_items)
        + sum(not u.evidence_verified for u in unresolved)
    )
    if unverified:
        warnings.append(f"{unverified} item(s) cite evidence not found in the transcript")

    return NormalisedExtraction(
        summary=_clean(extraction.summary) or "",
        key_points=[p for kp in extraction.key_points if (p := _clean(kp))],
        participants=list(seen.values()),
        decisions=decisions,
        action_items=action_items,
        keywords=_dedupe(extraction.keywords, _KEYWORD_MAX, _MAX_KEYWORDS),
        speakers=list(speakers.values()),
        unresolved_items=unresolved,
        next_steps=_dedupe(extraction.next_steps),
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
    decisions: int
    action_items: int
    participants: int
    warnings: list[str]

    def as_job_result(self) -> dict:
        return {
            "cached": self.cached,
            "decisions": self.decisions,
            "action_items": self.action_items,
            "participants": self.participants,
            "warnings": self.warnings,
        }


class MeetingNotFoundError(NotFoundError):
    code = "meeting_not_found"
    message = "The meeting no longer exists."


async def require_transcript(db: AsyncSession, meeting_id: uuid.UUID) -> Transcript:
    transcript = await db.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
    if transcript is None:
        raise ConflictError(
            "Add a transcript or upload a recording before processing this meeting.",
            code="transcript_missing",
        )
    return transcript


async def is_result_current(db: AsyncSession, meeting: Meeting, *, model: str) -> bool:
    """True when stored results already reflect the current transcript, prompt, and model.

    Used by the API (answer immediately, queue nothing) and by the worker
    (a recovered job whose previous attempt actually finished just before its
    worker died is completed without a second LLM call).

    Deliberately independent of ``meeting.status``. The summary and its
    provenance are committed in the same transaction as every other result, so
    a matching summary alone proves a complete, current run. Status is not a
    reliable signal here: crash recovery resets it to ``queued`` precisely in
    the case this check exists for.
    """
    row = (
        await db.execute(
            select(
                MeetingSummary.transcript_sha256,
                MeetingSummary.prompt_version,
                MeetingSummary.model,
                Transcript.content_sha256,
            )
            .join(Transcript, Transcript.meeting_id == MeetingSummary.meeting_id)
            .where(MeetingSummary.meeting_id == meeting.id)
        )
    ).one_or_none()
    if row is None:
        return False
    summary_sha, prompt_version, summary_model, transcript_sha = row
    return (
        summary_sha == transcript_sha
        and prompt_version == EXTRACTION_PROMPT_VERSION
        and summary_model == model
    )


async def result_counts(db: AsyncSession, meeting_id: uuid.UUID) -> ProcessingOutcome:
    """Describe already-stored results (for a cache hit)."""
    decisions = await db.scalar(
        select(func.count()).select_from(Decision).where(Decision.meeting_id == meeting_id)
    )
    action_items = await db.scalar(
        select(func.count()).select_from(ActionItem).where(ActionItem.meeting_id == meeting_id)
    )
    participants = await db.scalar(
        select(func.count())
        .select_from(MeetingParticipant)
        .where(MeetingParticipant.meeting_id == meeting_id)
    )
    return ProcessingOutcome(
        meeting_id=meeting_id,
        cached=True,
        decisions=decisions or 0,
        action_items=action_items or 0,
        participants=participants or 0,
        warnings=[],
    )


async def set_meeting_status(
    db: AsyncSession, meeting_id: uuid.UUID, status: MeetingStatus
) -> None:
    # Core UPDATE: safe to call after a rollback, when ORM instances are expired
    # and touching them in async code would raise.
    await db.execute(update(Meeting).where(Meeting.id == meeting_id).values(status=status))
    await db.commit()


async def run_extraction(
    db: AsyncSession, *, meeting_id: uuid.UUID, llm: LLMProvider
) -> ProcessingOutcome:
    """Run the LLM pipeline for one meeting and store the results.

    Called by the background worker, which has already won exclusive ownership
    of the meeting's job (ADR 0008), so no in-progress check is needed here.

    On failure the transaction is rolled back and the exception propagates.
    Deciding the resulting meeting status is the caller's job, because only the
    caller knows whether the failure will be retried (QUEUED) or is final
    (FAILED).
    """
    meeting = await db.scalar(select(Meeting).where(Meeting.id == meeting_id))
    if meeting is None:
        raise MeetingNotFoundError()
    transcript = await require_transcript(db, meeting_id)

    # Read what the LLM call needs BEFORE committing: committing releases the
    # pooled connection for the whole (tens of seconds) LLM wait.
    title, meeting_date, agenda = meeting.title, meeting.meeting_date, meeting.description
    content, content_sha = transcript.content, transcript.content_sha256
    input_kind = "notes" if transcript.source == TranscriptSource.NOTES else "transcript"

    meeting.status = MeetingStatus.PROCESSING
    await db.commit()
    logger.info("extraction started", extra={"meeting_id": str(meeting_id)})

    try:
        result = await llm.generate_structured(
            system_instruction=EXTRACTION_SYSTEM_INSTRUCTION,
            prompt=build_extraction_prompt(
                title=title,
                meeting_date=meeting_date,
                transcript=content,
                agenda=agenda,
                input_kind=input_kind,
            ),
            schema=MeetingExtraction,
        )
        normalised = normalise_extraction(
            result.data,
            transcript=content,
            meeting_date=meeting_date.date(),
            input_kind=input_kind,
        )
        await _replace_results(db, meeting_id=meeting_id, normalised=normalised)
        db.add(
            MeetingSummary(
                meeting_id=meeting_id,
                summary_text=normalised.summary,
                key_points=normalised.key_points,
                keywords=normalised.keywords,
                speakers=[asdict(s) for s in normalised.speakers],
                unresolved_items=[asdict(u) for u in normalised.unresolved_items],
                next_steps=normalised.next_steps,
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
    except BaseException:
        # BaseException, not Exception: a cancelled task (worker shutdown) must
        # also roll back rather than leave a half-written transaction open.
        await db.rollback()
        raise

    logger.info(
        "extraction completed",
        extra={
            "meeting_id": str(meeting_id),
            "decisions": len(normalised.decisions),
            "action_items": len(normalised.action_items),
            "participants": len(normalised.participants),
            "warnings": len(normalised.warnings),
        },
    )
    return ProcessingOutcome(
        meeting_id=meeting_id,
        cached=False,
        decisions=len(normalised.decisions),
        action_items=len(normalised.action_items),
        participants=len(normalised.participants),
        warnings=normalised.warnings,
    )


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
