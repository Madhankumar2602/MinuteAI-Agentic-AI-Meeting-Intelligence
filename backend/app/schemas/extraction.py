"""The contract between MinuteAI and the LLM.

These models are sent to the provider as the required response schema AND used
to validate what comes back. They are deliberately kept loose (plain strings,
no length limits or regex patterns) for two reasons:

1. Provider schema support is a subset of JSON Schema. Constraints such as
   ``maxLength`` or ``pattern`` are unevenly supported and can make a request
   fail outright rather than just producing worse output.
2. Being strict here would turn a single malformed deadline into a failed run
   that loses every correct decision and action item alongside it.

Strictness is applied afterwards, field by field, in
``app.services.intelligence.normalise_extraction``: a bad deadline becomes
``None`` and the rest of the result survives.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ExtractedDecision(BaseModel):
    decision: str = Field(description="The decision that was agreed, as one clear sentence.")
    context: str | None = Field(
        description="Why it was decided or what it affects. Null if not stated."
    )
    evidence_quote: str | None = Field(
        description="A short verbatim excerpt from the transcript that shows this decision."
    )


class ExtractedActionItem(BaseModel):
    task: str = Field(description="What must be done, as an imperative phrase.")
    owner: str | None = Field(
        description=(
            "Name of the single person responsible, exactly as named in the transcript. "
            "Null if no specific person was assigned."
        )
    )
    deadline: str | None = Field(
        description=(
            "Due date as YYYY-MM-DD, resolved against the meeting date. "
            "Null if no deadline was stated."
        )
    )
    deadline_text: str | None = Field(
        description="The deadline wording exactly as spoken, e.g. 'by next Friday'. Null if none."
    )
    priority: Literal["low", "medium", "high"] | None = Field(
        description="Only if urgency or priority was explicitly expressed. Otherwise null."
    )
    evidence_quote: str | None = Field(
        description="A short verbatim excerpt from the transcript that shows this action item."
    )


class MeetingExtraction(BaseModel):
    summary: str = Field(description="A concise summary of the meeting in 3 to 6 sentences.")
    key_points: list[str] = Field(description="The most important discussion points.")
    participants: list[str] = Field(
        description="Names of the people who spoke or were referred to as attending."
    )
    decisions: list[ExtractedDecision]
    action_items: list[ExtractedActionItem]
