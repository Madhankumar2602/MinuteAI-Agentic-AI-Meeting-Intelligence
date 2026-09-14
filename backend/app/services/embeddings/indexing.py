"""Build and check a meeting's search index (its chunks and vectors).

Runs as a stage of the processing job, between transcription and extraction:

    [transcribe] ─► index ─► extract

Indexing comes before extraction because it is local and deterministic: a
meeting becomes searchable even when the LLM is rate-limited or unavailable.
It is idempotent: when the stored chunks already match the transcript, the
model, and the chunker version, nothing is recomputed, so job retries and
crash recovery are cheap.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import MeetingChunk, Transcript
from app.services.embeddings.base import EmbeddingProvider
from app.services.embeddings.chunking import CHUNKER_VERSION, chunk_transcript
from app.services.intelligence import require_transcript

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IndexOutcome:
    chunks: int
    tokens: int
    cached: bool
    latency_ms: int


def chunk_is_current(transcript_sha256: str, model: str):
    """SQL condition: a chunk belongs to this transcript, model, and chunker."""
    return and_(
        MeetingChunk.transcript_sha256 == transcript_sha256,
        MeetingChunk.embedding_model == model,
        MeetingChunk.chunker_version == CHUNKER_VERSION,
    )


async def is_index_current(
    db: AsyncSession, meeting_id: uuid.UUID, *, embedder: EmbeddingProvider
) -> bool:
    """True when every stored chunk is current and there is at least one.

    A meeting without a transcript has nothing to index and reports False.
    """
    sha = await db.scalar(
        select(Transcript.content_sha256).where(Transcript.meeting_id == meeting_id)
    )
    if sha is None:
        return False
    total, current = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(chunk_is_current(sha, embedder.model)),
            ).where(MeetingChunk.meeting_id == meeting_id)
        )
    ).one()
    return total > 0 and total == current


async def index_meeting(
    db: AsyncSession, *, meeting_id: uuid.UUID, embedder: EmbeddingProvider
) -> IndexOutcome:
    """Chunk and embed the meeting's transcript, replacing any previous chunks."""
    started = time.perf_counter()
    if await is_index_current(db, meeting_id, embedder=embedder):
        count, tokens = (
            await db.execute(
                select(func.count(), func.coalesce(func.sum(MeetingChunk.token_count), 0)).where(
                    MeetingChunk.meeting_id == meeting_id
                )
            )
        ).one()
        return IndexOutcome(chunks=count, tokens=tokens, cached=True, latency_ms=0)

    transcript = await require_transcript(db, meeting_id)
    content, sha = transcript.content, transcript.content_sha256
    # Release the connection while the CPU work runs (seconds for long meetings).
    await db.commit()

    chunks = await asyncio.to_thread(chunk_transcript, content, embedder.count_tokens)
    vectors = await embedder.embed_documents([c.content for c in chunks])

    try:
        await db.execute(delete(MeetingChunk).where(MeetingChunk.meeting_id == meeting_id))
        db.add_all(
            MeetingChunk(
                meeting_id=meeting_id,
                chunk_index=c.index,
                content=c.content,
                char_start=c.char_start,
                char_end=c.char_end,
                token_count=c.token_count,
                transcript_sha256=sha,
                embedding_model=embedder.model,
                chunker_version=CHUNKER_VERSION,
                embedding=v,
            )
            for c, v in zip(chunks, vectors, strict=True)
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise

    outcome = IndexOutcome(
        chunks=len(chunks),
        tokens=sum(c.token_count for c in chunks),
        cached=False,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    logger.info(
        "meeting indexed",
        extra={
            "meeting_id": str(meeting_id),
            "chunks": outcome.chunks,
            "tokens": outcome.tokens,
            "latency_ms": outcome.latency_ms,
        },
    )
    return outcome
