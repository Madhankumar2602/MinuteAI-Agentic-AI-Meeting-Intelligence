"""Transcript chunks with their embeddings (M6, ADR 0002 and ADR 0012)."""

from __future__ import annotations

import enum
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

EMBEDDING_DIMENSIONS = 384


class ChunkSource(enum.StrEnum):
    """What a chunk was built from (M8).

    ``transcript`` passages are exact slices of the transcript (M6). The others
    are embedded from the meeting's Minutes of Meeting so questions about
    decisions, owners, and open items retrieve the structured answer, not only
    the conversation around it.
    """

    TRANSCRIPT = "transcript"
    SUMMARY = "summary"  # executive summary, key points, keywords
    DECISION = "decision"  # one per decision; source_ref = decisions.id
    ACTION_ITEM = "action_item"  # one per action item; source_ref = action_items.id
    PENDING = "pending"  # one per unresolved item
    NEXT_STEPS = "next_steps"  # all next steps together


MINUTES_SOURCES = tuple(s for s in ChunkSource if s is not ChunkSource.TRANSCRIPT)


class MeetingChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One passage of a transcript, embedded for semantic search.

    ``content`` is an exact slice of the transcript (``char_start:char_end``),
    so a search result or a RAG citation can point at the precise place in the
    transcript it came from.

    The vector lives on this row rather than in a separate table: the
    relationship is 1:1 and this is the hottest read in the RAG path.
    Ownership is not copied here; searches join ``meetings`` so access control
    stays one rule in one place (ADR 0002).
    """

    __tablename__ = "meeting_chunks"
    __table_args__ = (
        UniqueConstraint(
            "meeting_id",
            "source_kind",
            "chunk_index",
            name="uq_meeting_chunks_meeting_id_source_kind_chunk_index",
        ),
        Index(
            "ix_meeting_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    source_kind: Mapped[ChunkSource] = mapped_column(
        Enum(
            ChunkSource,
            name="chunk_source",
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            create_constraint=True,
            length=16,
        ),
        nullable=False,
        default=ChunkSource.TRANSCRIPT,
        server_default=ChunkSource.TRANSCRIPT.value,
    )
    # The decision or action item a minutes chunk describes. Not a foreign key:
    # it points at one of two tables. Answers re-read the row, so they show its
    # current owner, deadline, and status rather than the indexed wording.
    source_ref: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Offsets into the transcript; only transcript chunks have them.
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- provenance: a chunk is only valid for this exact input ------------
    # A chunk whose sha no longer matches the transcript, or whose model or
    # chunker version differs from the current one, is stale. Searches ignore
    # stale chunks, so an edited transcript can never surface old text.
    transcript_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(160), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(16), nullable=False)

    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
