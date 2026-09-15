"""Ask your meetings (M8): the LLM answer contract and the API response."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import ChunkSource

# ---------------------------------------------------------------------------
# Sent to the model as the required response schema
# ---------------------------------------------------------------------------


class GroundedAnswer(BaseModel):
    """What the model must return. Validated again in ``services/rag.py``."""

    answerable: bool = Field(
        description=(
            "True only if the numbered sources contain enough information to answer. "
            "False if the answer is not in the sources, even partly guessable."
        )
    )
    answer: str = Field(
        description=(
            "The answer, using only the sources, with [n] after every statement that "
            "comes from source n. If not answerable, one sentence saying what is missing."
        )
    )
    cited_sources: list[int] = Field(
        description="The numbers of every source the answer relies on."
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    meeting_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=50,
        description="Only search these meetings (each must be accessible). Default: all.",
    )


class AskSource(BaseModel):
    number: int = Field(description="The [n] used in the answer.")
    meeting_id: uuid.UUID
    meeting_title: str
    meeting_date: datetime
    kind: ChunkSource
    text: str = Field(description="What the model was shown for this source.")
    char_start: int | None = Field(description="Transcript offsets, for transcript sources.")
    char_end: int | None
    score: float


AskStatus = Literal["answered", "insufficient_context", "no_indexed_meetings"]


class AskResponse(BaseModel):
    question: str
    status: AskStatus
    answer: str
    sources: list[AskSource] = Field(description="Only the sources the answer cites.")
    retrieved: int = Field(description="Passages retrieved and shown to the model.")
    model: str | None = Field(description="The answering model; null if it was not called.")
    prompt_version: str
    latency_ms: int
