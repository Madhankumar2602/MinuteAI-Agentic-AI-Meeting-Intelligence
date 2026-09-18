"""The agent's observation tools: find situations that need a follow-up.

Every detector is ordinary SQL over the user's own records (the ownership rule
is a JOIN on ``meetings.owner_id``), so what the agent can act on is decided by
code, never by the model:

    find_overdue_action_items      open, deadline passed
    find_due_soon_action_items     open, due within AGENT_DUE_SOON_DAYS
    find_unassigned_action_items   open, nobody owns it
    find_open_decisions            still open AGENT_DECISION_STALE_DAYS after the meeting
    find_unresolved_topics         pending items from recent minutes; a topic that
                                   semantic search finds raised again in another
                                   meeting becomes a *recurring* topic

Each finding becomes a ``Candidate`` with a stable ``dedupe_key`` (so the same
situation is never proposed twice) and the facts the draft may use.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    OPEN_ACTION_STATUSES,
    ActionItem,
    ActionItemPriority,
    ChunkSource,
    Decision,
    DecisionStatus,
    Meeting,
    MeetingParticipant,
    MeetingSummary,
    ProposalKind,
    ProposalPriority,
)
from app.services.embeddings.base import EmbeddingProvider
from app.services.embeddings.search import semantic_search

# Order in which kinds are considered when a run has more findings than it may
# act on: late work first, open topics last.
KIND_RANK = {
    ProposalKind.OVERDUE_ACTION: 0,
    ProposalKind.DUE_SOON_ACTION: 1,
    ProposalKind.RECURRING_TOPIC: 2,
    ProposalKind.UNASSIGNED_ACTION: 3,
    ProposalKind.OPEN_DECISION: 4,
    ProposalKind.UNRESOLVED_TOPIC: 5,
}
KIND_LABEL = {
    ProposalKind.OVERDUE_ACTION: "overdue action item",
    ProposalKind.DUE_SOON_ACTION: "action item due soon",
    ProposalKind.UNASSIGNED_ACTION: "action item with no owner",
    ProposalKind.OPEN_DECISION: "decision still open",
    ProposalKind.UNRESOLVED_TOPIC: "unresolved topic",
    ProposalKind.RECURRING_TOPIC: "unresolved topic raised in several meetings",
}


@dataclass(slots=True)
class Candidate:
    kind: ProposalKind
    dedupe_key: str
    meeting_id: uuid.UUID
    meeting_title: str
    meeting_date: datetime
    title: str
    facts: list[str]
    allowed_recipients: list[str]
    query: str  # what to recall from past meetings for this situation
    priority: ProposalPriority  # the deterministic baseline
    urgency: int = 0  # tie-breaker within a kind: bigger is more urgent
    action_item_id: uuid.UUID | None = None
    decision_id: uuid.UUID | None = None
    related_meetings: list[str] = field(default_factory=list)

    @property
    def meeting_label(self) -> str:
        return f"{self.meeting_title} · {self.meeting_date.date().isoformat()}"

    def sort_key(self) -> tuple:
        return (KIND_RANK[self.kind], -self.urgency, self.meeting_date)


def _topic_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.casefold().split()).encode()).hexdigest()[:16]


async def _participants(
    db: AsyncSession, meeting_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    if not meeting_ids:
        return {}
    rows = await db.execute(
        select(MeetingParticipant.meeting_id, MeetingParticipant.display_name)
        .where(MeetingParticipant.meeting_id.in_(meeting_ids))
        .order_by(MeetingParticipant.display_name)
    )
    out: dict[uuid.UUID, list[str]] = {}
    for meeting_id, name in rows:
        out.setdefault(meeting_id, []).append(name)
    return out


def _deadline_fact(item: ActionItem) -> str:
    text = item.deadline.isoformat() if item.deadline else "none"
    if item.deadline_text:
        text += f' ("{item.deadline_text}")'
    return text


# ---------------------------------------------------------------------------
# Action items
# ---------------------------------------------------------------------------


async def find_action_item_candidates(
    db: AsyncSession, *, owner_id: uuid.UUID, today: date
) -> list[Candidate]:
    """Overdue, due-soon, and unassigned open action items (one candidate per item).

    An item that is both overdue and unassigned is reported once, as its most
    urgent situation: overdue, then due soon, then unassigned.
    """
    rows = (
        await db.execute(
            select(ActionItem, Meeting.title, Meeting.meeting_date)
            .join(Meeting, Meeting.id == ActionItem.meeting_id)
            .where(Meeting.owner_id == owner_id, ActionItem.status.in_(OPEN_ACTION_STATUSES))
        )
    ).all()
    participants = await _participants(db, {item.meeting_id for item, _, _ in rows})
    soon = today + timedelta(days=settings.agent_due_soon_days)

    candidates: list[Candidate] = []
    for item, title, meeting_date in rows:
        facts = [
            f"Task: {item.task}",
            f"Owner: {item.owner_name or 'nobody assigned'}",
            f"Deadline: {_deadline_fact(item)}",
            f"Status: {item.status.value}",
        ]
        recipients = [item.owner_name] if item.owner_name else participants.get(item.meeting_id, [])
        common = dict(
            meeting_id=item.meeting_id,
            meeting_title=title,
            meeting_date=meeting_date,
            allowed_recipients=recipients,
            query=item.task,
            action_item_id=item.id,
        )
        urgent = item.priority == ActionItemPriority.HIGH

        if item.deadline is not None and item.deadline < today:
            days = (today - item.deadline).days
            candidates.append(
                Candidate(
                    kind=ProposalKind.OVERDUE_ACTION,
                    dedupe_key=f"overdue:{item.id}:{item.deadline.isoformat()}",
                    title=f"Overdue by {days} day{'s' if days != 1 else ''}: {item.task}",
                    facts=[*facts, f"Days overdue: {days}"],
                    priority=ProposalPriority.HIGH
                    if days >= 7 or urgent
                    else ProposalPriority.MEDIUM,
                    urgency=days,
                    **common,
                )
            )
        elif item.deadline is not None and item.deadline <= soon:
            days = (item.deadline - today).days
            when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
            candidates.append(
                Candidate(
                    kind=ProposalKind.DUE_SOON_ACTION,
                    dedupe_key=f"due_soon:{item.id}:{item.deadline.isoformat()}",
                    title=f"Due {when}: {item.task}",
                    facts=[*facts, f"Due {when}"],
                    priority=ProposalPriority.HIGH if urgent else ProposalPriority.MEDIUM,
                    urgency=-days,
                    **common,
                )
            )
        elif not item.owner_name:
            candidates.append(
                Candidate(
                    kind=ProposalKind.UNASSIGNED_ACTION,
                    dedupe_key=f"unassigned:{item.id}",
                    title=f"No owner: {item.task}",
                    facts=facts,
                    priority=ProposalPriority.MEDIUM,
                    **common,
                )
            )
    return candidates


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


async def find_open_decisions(
    db: AsyncSession, *, owner_id: uuid.UUID, today: date
) -> list[Candidate]:
    cutoff = datetime.combine(
        today - timedelta(days=settings.agent_decision_stale_days), datetime.min.time(), UTC
    )
    rows = (
        await db.execute(
            select(Decision, Meeting.title, Meeting.meeting_date)
            .join(Meeting, Meeting.id == Decision.meeting_id)
            .where(
                Meeting.owner_id == owner_id,
                Decision.status == DecisionStatus.OPEN,
                Meeting.meeting_date <= cutoff,
            )
        )
    ).all()
    participants = await _participants(db, {d.meeting_id for d, _, _ in rows})
    candidates = []
    for decision, title, meeting_date in rows:
        age = (today - meeting_date.date()).days
        facts = [
            f"Decision: {decision.decision_text}",
            f"Status: open, {age} days after the meeting",
        ]
        if decision.context:
            facts.insert(1, f"Context: {decision.context}")
        candidates.append(
            Candidate(
                kind=ProposalKind.OPEN_DECISION,
                dedupe_key=f"decision:{decision.id}",
                meeting_id=decision.meeting_id,
                meeting_title=title,
                meeting_date=meeting_date,
                title=f"Confirm decision: {decision.decision_text}",
                facts=facts,
                allowed_recipients=participants.get(decision.meeting_id, []),
                query=decision.decision_text,
                priority=ProposalPriority.LOW,
                urgency=age,
                decision_id=decision.id,
            )
        )
    return candidates


# ---------------------------------------------------------------------------
# Unresolved and recurring topics
# ---------------------------------------------------------------------------


async def find_topic_candidates(
    db: AsyncSession, *, owner_id: uuid.UUID, today: date, embedder: EmbeddingProvider
) -> list[Candidate]:
    """Pending items from recent minutes; recurring when raised in another meeting.

    Newest meetings are examined first. When a topic is found again in an older
    meeting's pending items, that older item is marked as covered so the same
    topic produces one candidate, not one per meeting.
    """
    since = datetime.combine(
        today - timedelta(days=settings.agent_lookback_days), datetime.min.time(), UTC
    )
    rows = (
        await db.execute(
            select(Meeting.id, Meeting.title, Meeting.meeting_date, MeetingSummary.unresolved_items)
            .join(MeetingSummary, MeetingSummary.meeting_id == Meeting.id)
            .where(Meeting.owner_id == owner_id, Meeting.meeting_date >= since)
            .order_by(Meeting.meeting_date.desc())
        )
    ).all()
    participants = await _participants(db, {r[0] for r in rows})

    covered: set[tuple[uuid.UUID, int]] = set()
    candidates: list[Candidate] = []
    for meeting_id, title, meeting_date, items in rows:
        for index, entry in enumerate(items or []):
            if (meeting_id, index) in covered:
                continue
            text = entry.get("item", "").strip()
            if not text:
                continue
            related: dict[uuid.UUID, str] = {}
            hits = await semantic_search(
                db,
                owner_id=owner_id,
                query_vector=await embedder.embed_query(text),
                model=embedder.model,
                limit=10,
                sources=(ChunkSource.PENDING,),
            )
            for hit in hits:
                if hit.meeting_id == meeting_id or hit.score < settings.agent_recurring_min_score:
                    continue
                related[hit.meeting_id] = (
                    f"{hit.meeting_title} · {hit.meeting_date.date().isoformat()}"
                )
                covered.add((hit.meeting_id, hit.chunk_index))

            facts = [f"Unresolved: {text}"]
            if related:
                facts.append("Also raised in: " + "; ".join(related.values()))
                kind, priority = ProposalKind.RECURRING_TOPIC, ProposalPriority.MEDIUM
                key = f"recurring:{_topic_hash(text)}"
                label = f"Raised in {len(related) + 1} meetings: {text}"
            else:
                kind, priority = ProposalKind.UNRESOLVED_TOPIC, ProposalPriority.LOW
                key = f"topic:{meeting_id}:{_topic_hash(text)}"
                label = f"Unresolved: {text}"
            candidates.append(
                Candidate(
                    kind=kind,
                    dedupe_key=key,
                    meeting_id=meeting_id,
                    meeting_title=title,
                    meeting_date=meeting_date,
                    title=label,
                    facts=facts,
                    allowed_recipients=participants.get(meeting_id, []),
                    query=text,
                    priority=priority,
                    urgency=len(related),
                    related_meetings=list(related.values()),
                )
            )
    return candidates


async def find_candidates(
    db: AsyncSession, *, owner_id: uuid.UUID, today: date, embedder: EmbeddingProvider
) -> list[Candidate]:
    """Everything that may need a follow-up, most urgent first."""
    found = [
        *await find_action_item_candidates(db, owner_id=owner_id, today=today),
        *await find_open_decisions(db, owner_id=owner_id, today=today),
        *await find_topic_candidates(db, owner_id=owner_id, today=today, embedder=embedder),
    ]
    return sorted(found, key=Candidate.sort_key)
