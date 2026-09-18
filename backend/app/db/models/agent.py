"""The follow-up agent's records (M9, ADR 0015).

    agent_runs            one row per run: status, which tools ran, what they
                          found, and what was proposed (the audit trail)
    follow_up_proposals   what the agent suggests doing, waiting for a person
                          to approve (optionally edited) or reject

Proposals live in PostgreSQL, next to the action items, decisions, and meetings
they are about: they reference those rows, are deleted with their meeting, and
are read through the same ownership rule as everything else. The run trace sits
beside them so the explanation of *why* something was proposed can never
disagree with the proposal itself.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


def _enum(cls: type[enum.StrEnum], name: str, length: int = 24) -> Enum:
    return Enum(
        cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        create_constraint=True,
        length=length,
    )


class AgentRunStatus(enum.StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ProposalKind(enum.StrEnum):
    OVERDUE_ACTION = "overdue_action"  # open action item past its deadline
    DUE_SOON_ACTION = "due_soon_action"  # open action item due within days
    UNASSIGNED_ACTION = "unassigned_action"  # open action item nobody owns
    OPEN_DECISION = "open_decision"  # decision still open long after the meeting
    UNRESOLVED_TOPIC = "unresolved_topic"  # pending item from the minutes
    RECURRING_TOPIC = "recurring_topic"  # pending item raised in several meetings


class ProposalPriority(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProposalStatus(enum.StrEnum):
    PROPOSED = "proposed"  # waiting for a person
    APPROVED = "approved"  # a person accepted it (possibly after editing)
    REJECTED = "rejected"  # a person declined it; never proposed again


class DraftSource(enum.StrEnum):
    AI = "ai"  # drafted by the model and verified
    TEMPLATE = "template"  # the model was unavailable; a fixed template was used


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        # One run at a time per user, enforced by the database: two clicks, or
        # a click and a scheduler, cannot run the agent concurrently.
        Index(
            "uq_agent_runs_one_running_per_owner",
            "owner_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_agent_runs_owner_id_started_at", "owner_id", "started_at"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    status: Mapped[AgentRunStatus] = mapped_column(
        _enum(AgentRunStatus, "agent_run_status", 16),
        nullable=False,
        default=AgentRunStatus.RUNNING,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    candidates_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    proposals_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # [{step, at, ...detail}] in order: the complete, human-readable record of
    # what the agent looked at and why it proposed what it did.
    steps: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FollowUpProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "follow_up_proposals"
    __table_args__ = (
        # The agent never proposes the same thing twice, including something a
        # person already rejected. The key encodes what makes a situation new
        # (e.g. an overdue item's deadline), so a rescheduled task can come back.
        UniqueConstraint(
            "owner_id", "dedupe_key", name="uq_follow_up_proposals_owner_id_dedupe_key"
        ),
        Index("ix_follow_up_proposals_owner_id_status", "owner_id", "status"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: re-processing a meeting replaces its action items
    # and decisions, and an approved follow-up must survive that as history.
    action_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("action_items.id", ondelete="SET NULL"), nullable=True
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[ProposalKind] = mapped_column(_enum(ProposalKind, "proposal_kind"), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[ProposalPriority] = mapped_column(
        _enum(ProposalPriority, "proposal_priority", 16), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    recipients: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    draft_subject: Mapped[str] = mapped_column(String(300), nullable=False)
    draft_body: Mapped[str] = mapped_column(Text, nullable=False)
    drafted_by: Mapped[DraftSource] = mapped_column(
        _enum(DraftSource, "draft_source", 16), nullable=False
    )
    # The passages the draft relies on: [{number, meeting_id, meeting_title,
    # meeting_date, kind, text, char_start, char_end, score}], as in /ask.
    sources: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)

    status: Mapped[ProposalStatus] = mapped_column(
        _enum(ProposalStatus, "proposal_status", 16),
        nullable=False,
        default=ProposalStatus.PROPOSED,
    )
    # What the person approved, which may differ from the draft.
    final_subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    final_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
