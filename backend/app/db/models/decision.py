"""Decision model."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DecisionStatus(enum.StrEnum):
    OPEN = "open"  # agreed, not yet carried out / confirmed
    RESOLVED = "resolved"  # carried out or closed
    SUPERSEDED = "superseded"  # replaced by a later decision


class Decision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "decisions"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Order as extracted, so the UI shows decisions in meeting order.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_text: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Grounding: the transcript passage the model cited, and whether that
    # passage was actually found in the transcript. See services/grounding.py.
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[DecisionStatus] = mapped_column(
        Enum(
            DecisionStatus,
            name="decision_status",
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            length=16,
        ),
        default=DecisionStatus.OPEN,
        nullable=False,
        index=True,
    )
