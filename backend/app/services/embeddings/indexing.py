"""Build and check a meeting's search index (its chunks and vectors).

Two kinds of chunk, both in ``meeting_chunks`` (one table, one HNSW index):

    transcript passages   exact slices of the transcript            (M6)
    minutes passages      summary · each decision · each action item (M8)
                          · each pending item · next steps

They are built at different points of the processing job:

    [transcribe] ─► index_meeting (transcript) ─► extract ─► index_minutes ─► PDF

Transcript indexing comes before extraction because it is local and
deterministic: a meeting becomes searchable even when the LLM is unavailable.
Minutes can only be indexed once they exist.

Both are idempotent: when the stored chunks already match their inputs, nothing
is recomputed, so job retries and crash recovery are cheap.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    MINUTES_SOURCES,
    ActionItem,
    ChunkSource,
    Decision,
    Meeting,
    MeetingChunk,
    MeetingSummary,
    Transcript,
)
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


# ---------------------------------------------------------------------------
# Transcript passages (M6)
# ---------------------------------------------------------------------------


async def is_index_current(
    db: AsyncSession, meeting_id: uuid.UUID, *, embedder: EmbeddingProvider
) -> bool:
    """True when every transcript chunk is current and there is at least one.

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
            ).where(
                MeetingChunk.meeting_id == meeting_id,
                MeetingChunk.source_kind == ChunkSource.TRANSCRIPT,
            )
        )
    ).one()
    return total > 0 and total == current


async def index_meeting(
    db: AsyncSession, *, meeting_id: uuid.UUID, embedder: EmbeddingProvider
) -> IndexOutcome:
    """Chunk and embed the meeting's transcript, replacing its previous transcript chunks."""
    started = time.perf_counter()
    transcript_only = (
        MeetingChunk.meeting_id == meeting_id,
        MeetingChunk.source_kind == ChunkSource.TRANSCRIPT,
    )
    if await is_index_current(db, meeting_id, embedder=embedder):
        count, tokens = (
            await db.execute(
                select(func.count(), func.coalesce(func.sum(MeetingChunk.token_count), 0)).where(
                    *transcript_only
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
        await db.execute(delete(MeetingChunk).where(*transcript_only))
        db.add_all(
            MeetingChunk(
                meeting_id=meeting_id,
                source_kind=ChunkSource.TRANSCRIPT,
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


# ---------------------------------------------------------------------------
# Minutes passages (M8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MinutesDocument:
    kind: ChunkSource
    index: int
    content: str
    ref: uuid.UUID | None = None


async def minutes_documents(
    db: AsyncSession, meeting_id: uuid.UUID
) -> tuple[str, list[MinutesDocument]] | None:
    """The texts to embed for a meeting's minutes, and the transcript they came from.

    Built from the stored extraction, the same records the MOM and its PDF use.
    Each text starts with the meeting title so questions naming a meeting find
    it. Volatile fields (status) are left out: a status change does not need a
    re-index, and answers read the live status from the record itself.

    Returns None when the meeting has no current minutes.
    """
    row = (
        await db.execute(
            select(Meeting.title, MeetingSummary)
            .join(MeetingSummary, MeetingSummary.meeting_id == Meeting.id)
            .where(Meeting.id == meeting_id)
        )
    ).one_or_none()
    if row is None:
        return None
    title, summary = row
    docs: list[MinutesDocument] = []

    parts = [f"{title}. Summary: {summary.summary_text}"]
    if summary.key_points:
        parts.append("Key points: " + "; ".join(summary.key_points))
    if summary.keywords:
        parts.append("Topics: " + ", ".join(summary.keywords))
    docs.append(MinutesDocument(ChunkSource.SUMMARY, 0, "\n".join(parts)))

    decisions = (
        await db.scalars(
            select(Decision).where(Decision.meeting_id == meeting_id).order_by(Decision.position)
        )
    ).all()
    for i, d in enumerate(decisions):
        text = f"{title}. Decision: {d.decision_text}"
        if d.context:
            text += f" Context: {d.context}"
        docs.append(MinutesDocument(ChunkSource.DECISION, i, text, d.id))

    items = (
        await db.scalars(
            select(ActionItem)
            .where(ActionItem.meeting_id == meeting_id)
            .order_by(ActionItem.position)
        )
    ).all()
    for i, a in enumerate(items):
        text = f"{title}. Action item: {a.task}. Owner: {a.owner_name or 'unassigned'}."
        if a.deadline or a.deadline_text:
            text += f" Deadline: {a.deadline.isoformat() if a.deadline else a.deadline_text}."
        docs.append(MinutesDocument(ChunkSource.ACTION_ITEM, i, text, a.id))

    for i, u in enumerate(summary.unresolved_items):
        docs.append(
            MinutesDocument(ChunkSource.PENDING, i, f"{title}. Unresolved / pending: {u['item']}")
        )
    if summary.next_steps:
        docs.append(
            MinutesDocument(
                ChunkSource.NEXT_STEPS, 0, f"{title}. Next steps: " + "; ".join(summary.next_steps)
            )
        )
    return summary.transcript_sha256, docs


def _signature(sha: str, model: str, docs: list[tuple]) -> list[tuple]:
    return sorted((sha, model, *d) for d in docs)


async def is_minutes_index_current(
    db: AsyncSession, meeting_id: uuid.UUID, *, embedder: EmbeddingProvider
) -> bool:
    """True when the stored minutes chunks match the current minutes exactly.

    A meeting without minutes has nothing to index and is trivially current.
    """
    built = await minutes_documents(db, meeting_id)
    if built is None:
        return True
    sha, docs = built
    stored = (
        await db.execute(
            select(
                MeetingChunk.transcript_sha256,
                MeetingChunk.embedding_model,
                MeetingChunk.source_kind,
                MeetingChunk.chunk_index,
                MeetingChunk.content,
                MeetingChunk.source_ref,
            ).where(
                MeetingChunk.meeting_id == meeting_id,
                MeetingChunk.source_kind.in_(MINUTES_SOURCES),
            )
        )
    ).all()
    expected = _signature(sha, embedder.model, [(d.kind, d.index, d.content, d.ref) for d in docs])
    return sorted(tuple(r) for r in stored) == expected


async def index_minutes(
    db: AsyncSession, *, meeting_id: uuid.UUID, embedder: EmbeddingProvider
) -> IndexOutcome:
    """Embed the meeting's minutes, replacing its previous minutes chunks."""
    started = time.perf_counter()
    built = await minutes_documents(db, meeting_id)
    minutes_only = (
        MeetingChunk.meeting_id == meeting_id,
        MeetingChunk.source_kind.in_(MINUTES_SOURCES),
    )
    if built is None:
        return IndexOutcome(chunks=0, tokens=0, cached=True, latency_ms=0)
    if await is_minutes_index_current(db, meeting_id, embedder=embedder):
        return IndexOutcome(chunks=len(built[1]), tokens=0, cached=True, latency_ms=0)

    sha, docs = built
    await db.commit()  # release the connection during embedding
    texts = [d.content for d in docs]
    tokens = embedder.count_tokens(texts)
    vectors = await embedder.embed_documents(texts)
    try:
        await db.execute(delete(MeetingChunk).where(*minutes_only))
        db.add_all(
            MeetingChunk(
                meeting_id=meeting_id,
                source_kind=d.kind,
                source_ref=d.ref,
                chunk_index=d.index,
                content=d.content,
                token_count=n,
                transcript_sha256=sha,
                embedding_model=embedder.model,
                chunker_version=CHUNKER_VERSION,
                embedding=v,
            )
            for d, n, v in zip(docs, tokens, vectors, strict=True)
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    outcome = IndexOutcome(
        chunks=len(docs),
        tokens=sum(tokens),
        cached=False,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    logger.info(
        "minutes indexed",
        extra={
            "meeting_id": str(meeting_id),
            "chunks": outcome.chunks,
            "latency_ms": outcome.latency_ms,
        },
    )
    return outcome
