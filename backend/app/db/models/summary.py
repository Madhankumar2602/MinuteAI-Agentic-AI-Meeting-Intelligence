"""Meeting summary model."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MeetingSummary(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """AI-generated summary plus the provenance needed to reproduce it."""

    __tablename__ = "summaries"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    # A short ordered list. JSONB rather than a child table: it is always read
    # and written as a whole and never queried by element.
    key_points: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    # --- Minutes of Meeting content (M7) -----------------------------------
    # Same reasoning as key_points: small, ordered, always read with the summary.
    keywords: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # [{name, contribution, turns, words}]: turns and words are counted from the
    # transcript; the contribution comes from the model.
    speakers: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # [{item, evidence_quote, evidence_verified}]
    unresolved_items: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    next_steps: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    # --- provenance -------------------------------------------------------
    # Which model, which prompt, and which exact transcript produced this.
    # Without these, M12's evaluation results cannot be reproduced or compared.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    transcript_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
