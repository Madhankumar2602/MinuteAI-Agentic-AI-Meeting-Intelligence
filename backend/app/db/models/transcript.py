"""Transcript model."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TranscriptSource(enum.StrEnum):
    MANUAL = "manual"  # pasted text          (M2)
    TRANSCRIPTION = "transcription"  # produced from audio  (M4)
    NOTES = "notes"  # meeting notes / description typed by a person  (M7)


# Text a person provided. It always takes precedence over a recording's
# automatic transcription and is never overwritten by it.
HUMAN_SOURCES = frozenset({TranscriptSource.MANUAL, TranscriptSource.NOTES})


class Transcript(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The text of one meeting. Exactly one per meeting, replaced in place.

    The full text lives in PostgreSQL rather than S3 in M2: it is typically
    well under 1 MB, it is read on every processing run, and in M6 it is the
    input to chunking. From M4 the raw provider output is additionally archived
    to S3, with this row remaining the working copy.
    """

    __tablename__ = "transcripts"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # SHA-256 of the content. Lets processing detect "nothing changed since the
    # last run" and skip a paid LLM call, and marks stored results as stale
    # when the transcript is edited afterwards.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Provenance for transcripts produced from a recording (M4). The etag pins the
    # exact object that was transcribed: replacing the recording changes it, so
    # the worker knows the transcript is out of date and transcribes again.
    media_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meeting_media.id", ondelete="SET NULL"), nullable=True
    )
    media_etag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Full structured transcription output (segments, speakers, timestamps),
    # archived in S3. This table keeps the plain-text working copy.
    raw_s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    transcription_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped[TranscriptSource] = mapped_column(
        Enum(
            TranscriptSource,
            name="transcript_source",
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            create_constraint=True,
            length=16,
        ),
        default=TranscriptSource.MANUAL,
        nullable=False,
    )
