"""Meeting participant model."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


def participant_name_key(name: str) -> str:
    """Case- and whitespace-insensitive identity for a spoken name."""
    return " ".join(name.split()).casefold()


class MeetingParticipant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A person named in a meeting.

    Participants are extracted from the transcript, so most will NOT be users
    of the system. ``user_id`` links one to an account when that is known, which
    the M8 agent needs to address a follow-up to the right person.
    """

    __tablename__ = "meeting_participants"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Normalised form backing the uniqueness constraint, so "Priya" and
    # "priya " are one participant rather than two.
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    __table_args__ = (UniqueConstraint("meeting_id", "name_key"),)
