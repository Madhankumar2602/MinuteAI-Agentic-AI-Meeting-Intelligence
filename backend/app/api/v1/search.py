"""Semantic search across the current user's meeting transcripts (M6)."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.services.authorization import AccessLevel, authorize_meeting_access
from app.services.embeddings import EmbeddingProvider, get_embedder
from app.services.embeddings.search import semantic_search

logger = get_logger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


class SearchResult(BaseModel):
    chunk_id: uuid.UUID
    meeting_id: uuid.UUID
    meeting_title: str
    meeting_date: datetime
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    score: float


class SearchResponse(BaseModel):
    query: str
    model: str
    results: list[SearchResult]


@router.get(
    "",
    response_model=SearchResponse,
    summary="Find transcript passages by meaning, not just matching words",
)
async def search(
    db: DbSession,
    current_user: CurrentUser,
    embedder: Annotated[EmbeddingProvider, Depends(get_embedder)],
    q: Annotated[str, Query(min_length=2, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    meeting_id: Annotated[uuid.UUID | None, Query(description="Search one meeting only")] = None,
) -> SearchResponse:
    """Results are ordered by cosine similarity (``score``, higher is closer).

    Only meetings the user can access are searched, and only chunks built from
    the meeting's current transcript. A meeting becomes searchable once it has
    been processed.
    """
    query = q.strip()
    if meeting_id is not None:
        # Same 404 as every other meeting route for a meeting the user cannot see.
        await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)

    started = time.perf_counter()
    vector = await embedder.embed_query(query)
    hits = await semantic_search(
        db,
        owner_id=current_user.id,
        query_vector=vector,
        model=embedder.model,
        limit=limit,
        meeting_id=meeting_id,
    )
    # The query text is user content and is not logged.
    logger.info(
        "search completed",
        extra={
            "results": len(hits),
            "scoped": meeting_id is not None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return SearchResponse(
        query=query,
        model=embedder.model,
        results=[SearchResult.model_validate(h, from_attributes=True) for h in hits],
    )
