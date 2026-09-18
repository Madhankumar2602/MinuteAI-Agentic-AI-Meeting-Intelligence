"""Ask your meetings: retrieval-augmented, grounded answers (M8, ADR 0014).

    question
       │ embed (local MiniLM)
       ▼
    semantic_search over ALL chunk kinds          one SQL statement: ranking +
    (transcript passages + minutes passages)      ownership join + stale filter
       │ keep score ≥ RAG_MIN_SCORE, ≤ RAG_MAX_PER_MEETING per meeting, top RAG_TOP_K
       │
       ├── nothing relevant ──► "not in your meetings"      (no LLM call)
       ▼
    build context from the SOURCE RECORDS         decisions / action items are
       │                                           re-read: current owner,
       ▼                                           deadline, status
    Gemini, schema GroundedAnswer (answerable, answer with [n], cited_sources)
       │
       ▼
    verify citations: numbers must be sources that were shown;
    invalid markers removed; "answerable" with no valid citation ⇒ not answered
       │
       ▼
    answer + only the cited sources (meeting, date, kind, text, offsets)

The LLM is never the only safeguard against invented answers. Two checks run
in code: the score gate before the call, and citation verification after it.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import (
    ActionItem,
    ChunkSource,
    Decision,
    Meeting,
    MeetingChunk,
    User,
)
from app.schemas.ask import AskResponse, AskSource, GroundedAnswer
from app.services.authorization import AccessLevel, authorize_meeting_access
from app.services.embeddings.base import EmbeddingProvider
from app.services.embeddings.search import SearchHit, semantic_search
from app.services.llm.base import LLMProvider
from app.services.prompts import ASK_PROMPT_VERSION, ASK_SYSTEM_INSTRUCTION, build_ask_prompt

logger = get_logger(__name__)

KIND_LABEL = {
    ChunkSource.TRANSCRIPT: "Transcript passage",
    ChunkSource.SUMMARY: "Minutes: summary",
    ChunkSource.DECISION: "Minutes: decision",
    ChunkSource.ACTION_ITEM: "Minutes: action item",
    ChunkSource.PENDING: "Minutes: pending item",
    ChunkSource.NEXT_STEPS: "Minutes: next steps",
}

NOT_FOUND_ANSWER = "I couldn't find this in your meetings."
UNSUPPORTED_ANSWER = (
    "I couldn't give an answer supported by your meetings: the response did not "
    "cite any of the retrieved sources."
)
NO_MEETINGS_ANSWER = (
    "None of your meetings are ready to search yet. Process a meeting first, then ask again."
)

# [3]  or  [1, 2]  or  [1,2,3]
_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass(frozen=True, slots=True)
class Source:
    number: int
    hit: SearchHit
    text: str


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def select_hits(
    hits: list[SearchHit], *, top_k: int, max_per_meeting: int, min_score: float
) -> list[SearchHit]:
    """Relevance gate and per-meeting diversity. ``hits`` arrive best first."""
    chosen: list[SearchHit] = []
    per_meeting: dict[uuid.UUID, int] = {}
    for hit in hits:
        if hit.score < min_score:
            break
        if per_meeting.get(hit.meeting_id, 0) >= max_per_meeting:
            continue
        per_meeting[hit.meeting_id] = per_meeting.get(hit.meeting_id, 0) + 1
        chosen.append(hit)
        if len(chosen) == top_k:
            break
    return chosen


async def build_sources(db: AsyncSession, hits: list[SearchHit]) -> list[Source]:
    """Turn retrieved chunks into the text the model sees.

    Decisions and action items are rendered from their current rows, so a task
    marked done after the meeting is reported as done. A chunk whose record no
    longer exists is dropped rather than shown with stale content.
    """
    decision_ids = [
        h.source_ref for h in hits if h.source_kind == ChunkSource.DECISION and h.source_ref
    ]
    action_ids = [
        h.source_ref for h in hits if h.source_kind == ChunkSource.ACTION_ITEM and h.source_ref
    ]
    decisions = (
        {d.id: d for d in await db.scalars(select(Decision).where(Decision.id.in_(decision_ids)))}
        if decision_ids
        else {}
    )
    actions = (
        {a.id: a for a in await db.scalars(select(ActionItem).where(ActionItem.id.in_(action_ids)))}
        if action_ids
        else {}
    )

    sources: list[Source] = []
    for hit in hits:
        if hit.source_kind == ChunkSource.DECISION:
            d = decisions.get(hit.source_ref)
            if d is None or d.meeting_id != hit.meeting_id:
                continue
            text = f"Decision: {d.decision_text}"
            if d.context:
                text += f"\nContext: {d.context}"
            text += f"\nStatus: {d.status.value}"
        elif hit.source_kind == ChunkSource.ACTION_ITEM:
            a = actions.get(hit.source_ref)
            if a is None or a.meeting_id != hit.meeting_id:
                continue
            deadline = a.deadline.isoformat() if a.deadline else "none"
            if a.deadline_text:
                deadline += f' ("{a.deadline_text}")'
            text = (
                f"Action item: {a.task}\nOwner: {a.owner_name or 'unassigned'}"
                f"\nDeadline: {deadline}\nStatus: {a.status.value}"
            )
        else:
            text = hit.content
        sources.append(Source(number=len(sources) + 1, hit=hit, text=text))
    return sources


def source_header(source: Source) -> str:
    """The line that introduces a source to the model: meeting, date, kind."""
    hit = source.hit
    return f"{hit.meeting_title} · {hit.meeting_date.date().isoformat()} · {KIND_LABEL[hit.source_kind]}"


async def retrieve_context(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    query: str,
    embedder: EmbeddingProvider,
    meeting_ids: list[uuid.UUID] | None = None,
    top_k: int | None = None,
) -> tuple[list[Source], int]:
    """The retrieval half of RAG, shared by Ask-your-meetings and the agent (M9).

    Returns (sources, candidates considered). Sources are numbered from 1, drawn
    from every chunk kind, limited to ``owner_id``'s meetings in the same SQL
    statement as the ranking, gated by relevance, capped per meeting, and built
    from live records for decisions and action items. Callers that scope by
    ``meeting_ids`` must authorise those meetings first.
    """
    k = top_k or settings.rag_top_k
    vector = await embedder.embed_query(query)
    candidates = await semantic_search(
        db,
        owner_id=owner_id,
        query_vector=vector,
        model=embedder.model,
        limit=k * 3,
        meeting_ids=meeting_ids,
        sources=tuple(ChunkSource),
    )
    hits = select_hits(
        candidates,
        top_k=k,
        max_per_meeting=settings.rag_max_per_meeting,
        min_score=settings.rag_min_score,
    )
    return await build_sources(db, hits), len(candidates)


# ---------------------------------------------------------------------------
# Grounding checks
# ---------------------------------------------------------------------------


def _tidy(text: str) -> str:
    """Collapse the gaps left where citation markers were removed."""
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()


def verify_citations(answer: GroundedAnswer, source_count: int) -> tuple[str, list[int]]:
    """Return (answer text with only valid citations, cited source numbers in order).

    A citation is valid only if it names a source that was actually shown.
    Invalid numbers are removed from the text instead of being passed on as if
    they were evidence; "[1, 2]" is normalised to "[1][2]".
    """
    cited: list[int] = []

    def rewrite(match: re.Match[str]) -> str:
        kept = []
        for part in match.group(1).split(","):
            n = int(part)
            if 1 <= n <= source_count:
                kept.append(n)
                if n not in cited:
                    cited.append(n)
        return "".join(f"[{n}]" for n in kept)

    text = _tidy(_CITATION.sub(rewrite, answer.answer))
    # Numbers listed but never placed in the text still count as relied upon.
    for n in answer.cited_sources:
        if 1 <= n <= source_count and n not in cited:
            cited.append(n)
    return text, cited


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def ask(
    db: AsyncSession,
    *,
    user: User,
    question: str,
    embedder: EmbeddingProvider,
    llm: LLMProvider,
    meeting_ids: list[uuid.UUID] | None = None,
) -> AskResponse:
    started = time.perf_counter()
    question = " ".join(question.split())

    if meeting_ids is not None:
        # Same rule and same 404 as every other meeting route.
        for meeting_id in dict.fromkeys(meeting_ids):
            await authorize_meeting_access(db, meeting_id, user, AccessLevel.READ)

    def respond(status, answer, sources=(), *, retrieved=0, model=None) -> AskResponse:
        response = AskResponse(
            question=question,
            status=status,
            answer=answer,
            sources=[
                AskSource(
                    number=s.number,
                    meeting_id=s.hit.meeting_id,
                    meeting_title=s.hit.meeting_title,
                    meeting_date=s.hit.meeting_date,
                    kind=s.hit.source_kind,
                    text=s.text,
                    char_start=s.hit.char_start,
                    char_end=s.hit.char_end,
                    score=s.hit.score,
                )
                for s in sources
            ],
            retrieved=retrieved,
            model=model,
            prompt_version=ASK_PROMPT_VERSION,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        # The question and answer are user content and are not logged.
        logger.info(
            "question answered",
            extra={
                "status": status,
                "retrieved": retrieved,
                "cited": len(response.sources),
                "scoped": meeting_ids is not None,
                "latency_ms": response.latency_ms,
            },
        )
        return response

    sources, candidate_count = await retrieve_context(
        db, owner_id=user.id, query=question, embedder=embedder, meeting_ids=meeting_ids
    )

    if not sources:
        indexed = await db.scalar(
            select(
                exists().where(
                    MeetingChunk.meeting_id == Meeting.id,
                    Meeting.owner_id == user.id,
                )
            )
        )
        if not indexed:
            return respond("no_indexed_meetings", NO_MEETINGS_ANSWER)
        return respond("insufficient_context", NOT_FOUND_ANSWER, retrieved=candidate_count)

    result = await llm.generate_structured(
        system_instruction=ASK_SYSTEM_INSTRUCTION,
        prompt=build_ask_prompt(
            question=question, sources=[(s.number, source_header(s), s.text) for s in sources]
        ),
        schema=GroundedAnswer,
        temperature=0.0,
    )
    text, cited = verify_citations(result.data, len(sources))

    if not result.data.answerable:
        return respond(
            "insufficient_context",
            _tidy(_CITATION.sub("", text)) or NOT_FOUND_ANSWER,
            retrieved=len(sources),
            model=result.model,
        )
    if not cited:
        return respond(
            "insufficient_context", UNSUPPORTED_ANSWER, retrieved=len(sources), model=result.model
        )
    by_number = {s.number: s for s in sources}
    return respond(
        "answered",
        text,
        [by_number[n] for n in sorted(cited)],
        retrieved=len(sources),
        model=result.model,
    )
