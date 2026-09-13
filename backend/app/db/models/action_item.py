"""Action item model."""

from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ActionItemStatus(enum.StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class ActionItemPriority(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Statuses that still count as outstanding work. Shared by the overdue filter
# and, later, by the M8 agent's overdue detection.
OPEN_ACTION_STATUSES = (ActionItemStatus.PENDING, ActionItemStatus.IN_PROGRESS)


def _enum(cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(
        cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        length=16,
    )


class ActionItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "action_items"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    task: Mapped[str] = mapped_column(Text, nullable=False)

    # The name exactly as extracted is always kept, even when it cannot be
    # matched to a participant: losing "who" is worse than failing to link it.
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_participant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meeting_participants.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Resolved calendar date (when one can be determined) and the original
    # wording ("by next Friday"), so a wrong resolution can be spotted and fixed.
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline_text: Mapped[str | None] = mapped_column(String(255), nullable=True)

    priority: Mapped[ActionItemPriority | None] = mapped_column(
        _enum(ActionItemPriority, "action_item_priority"), nullable=True
    )
    status: Mapped[ActionItemStatus] = mapped_column(
        _enum(ActionItemStatus, "action_item_status"),
        default=ActionItemStatus.PENDING,
        nullable=False,
    )

    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        # "Open items past their deadline": the dashboard's overdue view and
        # the M8 agent's primary scan both filter on exactly these two columns.
        Index("ix_action_items_status_deadline", "status", "deadline"),
    )
