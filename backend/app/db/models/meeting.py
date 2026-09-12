"""Meeting model."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.user import User


class MeetingSourceType(enum.StrEnum):
    """How the meeting content arrived."""

    TEXT = "text"  # pasted / uploaded transcript  (M2)
    AUDIO = "audio"  # uploaded audio                (M4)
    VIDEO = "video"  # uploaded video                (M4)


class MeetingStatus(enum.StrEnum):
    """Lifecycle of the AI processing pipeline."""

    CREATED = "created"  # exists, no transcript yet
    PROCESSING = "processing"  # pipeline running        (M3)
    COMPLETED = "completed"  # summary/decisions ready (M2)
    FAILED = "failed"  # pipeline error          (M3)


# native_enum=False renders these as VARCHAR + CHECK rather than a PostgreSQL
# ENUM type. PostgreSQL ENUMs cannot gain a value inside a transaction block,
# which makes every future status addition an awkward migration; a CHECK
# constraint is simply dropped and recreated.
_SOURCE_TYPE = Enum(
    MeetingSourceType,
    name="meeting_source_type",
    values_callable=lambda e: [m.value for m in e],
    native_enum=False,
    length=16,
)
_STATUS = Enum(
    MeetingStatus,
    name="meeting_status",
    values_callable=lambda e: [m.value for m in e],
    native_enum=False,
    length=16,
)


class Meeting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "meetings"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When the meeting actually happened - distinct from created_at, which is
    # when the record was entered. RAG queries filter on this one.
    meeting_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    source_type: Mapped[MeetingSourceType] = mapped_column(
        _SOURCE_TYPE, default=MeetingSourceType.TEXT, nullable=False
    )
    status: Mapped[MeetingStatus] = mapped_column(
        _STATUS, default=MeetingStatus.CREATED, nullable=False, index=True
    )

    owner: Mapped[User] = relationship(back_populates="meetings", lazy="raise")

    __table_args__ = (
        # Serves the dashboard's "my meetings, newest first" query with one
        # index scan and no sort.
        Index("ix_meetings_owner_id_meeting_date", "owner_id", meeting_date.desc()),
    )

    def __repr__(self) -> str:
        return f"<Meeting {self.title!r} status={self.status.value}>"
