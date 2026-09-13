"""Uploaded meeting recording."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MeetingMedia(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The confirmed recording for a meeting. At most one per meeting.

    A row exists only for an upload that has been verified in storage: present,
    within the size limit, of the declared type, and with matching file
    signature bytes. Issued-but-unused upload URLs create no row (see
    ``api/v1/media.py``), so a replacement upload can never clobber the current
    recording before the new one is confirmed.

    PostgreSQL stores the object's key and verified metadata, never the bytes
    (ADR 0001): the recording lives in S3.
    """

    __tablename__ = "meeting_media"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    s3_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Read from storage when the upload is confirmed - never trusted from the client.
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    # Display only. Never used to build a key or a path.
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
