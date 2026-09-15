"""Ask your meetings: grounded answers with source references (M8)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, DbSession
from app.schemas.ask import AskRequest, AskResponse
from app.services import rag
from app.services.embeddings import EmbeddingProvider, get_embedder
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider

router = APIRouter(prefix="/ask", tags=["ask your meetings"])


@router.post(
    "",
    response_model=AskResponse,
    summary="Answer a question from your meetings, with cited sources",
)
async def ask_meetings(
    payload: AskRequest,
    db: DbSession,
    current_user: CurrentUser,
    embedder: Annotated[EmbeddingProvider, Depends(get_embedder)],
    llm: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> AskResponse:
    """Searches the transcripts and Minutes of Meeting of the meetings you can
    access, and answers **only** from what it finds.

    * ``answered``: every statement cites a numbered source, and ``sources``
      lists exactly the cited passages with their meeting.
    * ``insufficient_context``: nothing relevant was found, or the model could
      not support an answer from what was found. Nothing is invented.
    * ``no_indexed_meetings``: no processed meetings to search yet.
    """
    return await rag.ask(
        db,
        user=current_user,
        question=payload.question,
        embedder=embedder,
        llm=llm,
        meeting_ids=payload.meeting_ids,
    )
