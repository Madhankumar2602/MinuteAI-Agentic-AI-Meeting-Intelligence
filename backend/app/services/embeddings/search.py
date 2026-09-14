"""Semantic search over the current user's meetings (M6; the retrieval step of M7).

One SQL statement does ranking and access control together:

    SELECT chunk, meeting title, cosine distance
    FROM meeting_chunks
    JOIN meetings     ON owner = current user          ← authorization
    JOIN transcripts  ON chunk sha = transcript sha    ← stale chunks never match
    WHERE model = current model AND chunker = current version
    ORDER BY embedding <=> query_vector
    LIMIT k

Filtering inside the query, not in Python afterwards, is the point of ADR 0002:
over-fetching and filtering would silently return fewer than ``k`` results.

With an HNSW index there is a subtlety. The index returns its nearest
``ef_search`` candidates and PostgreSQL filters them afterwards, so if most
nearby vectors belong to other users, a user could get too few results.
pgvector 0.8 fixes this with iterative index scans: when filtering leaves too
few rows, the scan continues into the index. It is switched on per transaction
below, and tested with the index forced on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Meeting, MeetingChunk, Transcript
from app.services.embeddings.chunking import CHUNKER_VERSION

# Candidates the HNSW scan considers per step (pgvector default 40). Higher
# improves recall at a small latency cost; irrelevant at this corpus size, but
# the setting is where tuning would happen.
HNSW_EF_SEARCH = 100


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: uuid.UUID
    meeting_id: uuid.UUID
    meeting_title: str
    meeting_date: datetime
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    # Cosine similarity in [-1, 1]; vectors are normalised, so 1 - distance.
    score: float


async def semantic_search(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    query_vector: list[float],
    model: str,
    limit: int = 10,
    meeting_id: uuid.UUID | None = None,
) -> list[SearchHit]:
    # SET LOCAL lasts only until the end of the current transaction, so these
    # settings cannot leak to other requests sharing the pooled connection.
    await db.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
    await db.execute(text(f"SET LOCAL hnsw.ef_search = {int(HNSW_EF_SEARCH)}"))

    distance = MeetingChunk.embedding.cosine_distance(query_vector)
    stmt = (
        select(MeetingChunk, Meeting.title, Meeting.meeting_date, distance.label("distance"))
        .join(Meeting, Meeting.id == MeetingChunk.meeting_id)
        .join(
            Transcript,
            (Transcript.meeting_id == MeetingChunk.meeting_id)
            & (Transcript.content_sha256 == MeetingChunk.transcript_sha256),
        )
        .where(
            Meeting.owner_id == owner_id,
            MeetingChunk.embedding_model == model,
            MeetingChunk.chunker_version == CHUNKER_VERSION,
        )
        .order_by(distance)
        .limit(limit)
    )
    if meeting_id is not None:
        stmt = stmt.where(MeetingChunk.meeting_id == meeting_id)

    rows = (await db.execute(stmt)).all()
    return [
        SearchHit(
            chunk_id=chunk.id,
            meeting_id=chunk.meeting_id,
            meeting_title=title,
            meeting_date=meeting_date,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            score=round(1.0 - float(dist), 4),
        )
        for chunk, title, meeting_date, dist in rows
    ]
