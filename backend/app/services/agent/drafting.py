"""The agent's decide-and-draft step, and the checks applied to what the model says.

One structured model call covers every candidate in a run. The model may decide
(follow up or not, how urgent, why) and write (subject, message), but only
inside limits enforced here in code:

* only candidates that were given are accepted; invented ids are ignored;
* recipients are filtered to the candidate's allowed names;
* citations must name a source shown for that candidate;
* "no follow-up needed" is accepted only with a valid citation as evidence,
  otherwise the candidate is kept;
* empty or over-long text falls back to a fixed template.

If the model is unavailable, every candidate gets a template draft, so the
agent still produces useful, clearly-labelled proposals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import DraftSource, ProposalKind, ProposalPriority
from app.services.agent.detectors import Candidate
from app.services.rag import Source

MAX_SUBJECT = 200
MAX_BODY = 2000
_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


# ---------------------------------------------------------------------------
# The model's response contract
# ---------------------------------------------------------------------------


class DraftedFollowUp(BaseModel):
    candidate_id: str = Field(description="The candidate id exactly as given, e.g. C1.")
    follow_up: bool = Field(
        description="False only if a source shows the situation is already resolved."
    )
    priority: Literal["low", "medium", "high"]
    rationale: str = Field(description="One sentence; cite sources as [n].")
    recipients: list[str] = Field(description="Chosen only from the allowed recipients.")
    subject: str
    message: str = Field(description="The draft follow-up, first person, no citation markers.")
    cited_sources: list[int]


class AgentDrafts(BaseModel):
    drafts: list[DraftedFollowUp]


# ---------------------------------------------------------------------------
# Verified outcome
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DraftOutcome:
    candidate: Candidate
    follow_up: bool
    priority: ProposalPriority
    rationale: str
    recipients: list[str]
    subject: str
    body: str
    sources: list[Source]
    drafted_by: DraftSource
    adjustments: list[str] = field(default_factory=list)


def _tidy(text: str) -> str:
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()


def _valid_citations(text: str, listed: list[int], count: int) -> tuple[str, list[int]]:
    cited: list[int] = []

    def rewrite(match: re.Match[str]) -> str:
        kept = [int(p) for p in match.group(1).split(",") if 1 <= int(p) <= count]
        cited.extend(n for n in kept if n not in cited)
        return "".join(f"[{n}]" for n in kept)

    text = _tidy(_CITATION.sub(rewrite, text))
    cited.extend(n for n in listed if 1 <= n <= count and n not in cited)
    return text, cited


def template_draft(candidate: Candidate, sender: str) -> tuple[str, str]:
    """A plain, correct follow-up built only from the candidate's facts."""
    greeting = (
        f"Hi {', '.join(candidate.allowed_recipients)},"
        if candidate.allowed_recipients
        else "Hi all,"
    )
    origin = f"from our meeting “{candidate.meeting_title}” on {candidate.meeting_date.date().isoformat()}"
    facts = {f.split(":", 1)[0]: f.split(":", 1)[1].strip() for f in candidate.facts if ":" in f}
    match candidate.kind:
        case ProposalKind.OVERDUE_ACTION:
            subject = f"Update on: {facts.get('Task', candidate.title)}"
            ask = (
                f"The action item “{facts.get('Task')}” {origin} was due "
                f"{facts.get('Deadline', 'earlier')}. Could you share where it stands and when it will be done?"
            )
        case ProposalKind.DUE_SOON_ACTION:
            subject = f"Reminder: {facts.get('Task', candidate.title)}"
            ask = (
                f"A reminder that “{facts.get('Task')}” {origin} is due "
                f"{facts.get('Deadline', 'soon')}. Let me know if anything is blocking it."
            )
        case ProposalKind.UNASSIGNED_ACTION:
            subject = f"Owner needed: {facts.get('Task', candidate.title)}"
            ask = f"“{facts.get('Task')}” {origin} has no owner yet. Who can take it on?"
        case ProposalKind.OPEN_DECISION:
            subject = "Confirming a decision"
            ask = (
                f"We decided “{facts.get('Decision')}” {origin}. Has it been carried out, "
                "or does it need another look?"
            )
        case _:
            topic = facts.get("Unresolved", candidate.title)
            subject = f"Open question: {topic}"[:MAX_SUBJECT]
            also = facts.get("Also raised in")
            ask = (
                f"“{topic}” was left open {origin}"
                + (f" and came up again in {also}." if also else ".")
                + " Can we agree a way forward?"
            )
    return subject[:MAX_SUBJECT], f"{greeting}\n\n{ask}\n\nThanks,\n{sender}"


def template_outcome(
    candidate: Candidate, sources: list[Source], sender: str, reason: str
) -> DraftOutcome:
    subject, body = template_draft(candidate, sender)
    return DraftOutcome(
        candidate=candidate,
        follow_up=True,
        priority=candidate.priority,
        rationale=f"Found by the {candidate.kind.value.replace('_', ' ')} check.",
        recipients=list(candidate.allowed_recipients),
        subject=subject,
        body=body,
        sources=sources,
        drafted_by=DraftSource.TEMPLATE,
        adjustments=[reason],
    )


def verify_drafts(
    drafts: AgentDrafts,
    candidates: dict[str, Candidate],
    sources: dict[str, list[Source]],
    sender: str,
) -> tuple[list[DraftOutcome], list[str]]:
    """Apply every guardrail; return one outcome per candidate and a list of notes."""
    notes: list[str] = []
    by_id: dict[str, DraftedFollowUp] = {}
    for draft in drafts.drafts:
        if draft.candidate_id not in candidates:
            notes.append(f"ignored a draft for unknown candidate {draft.candidate_id!r}")
        elif draft.candidate_id in by_id:
            notes.append(f"ignored a second draft for {draft.candidate_id}")
        else:
            by_id[draft.candidate_id] = draft

    outcomes: list[DraftOutcome] = []
    for cid, candidate in candidates.items():
        shown = sources.get(cid, [])
        draft = by_id.get(cid)
        if draft is None:
            outcomes.append(
                template_outcome(candidate, shown, sender, "the model returned no draft")
            )
            continue

        adjustments: list[str] = []
        rationale, cited = _valid_citations(draft.rationale, draft.cited_sources, len(shown))
        if len(cited) < len({n for n in draft.cited_sources}):
            adjustments.append("removed citations to sources that were not shown")

        allowed = {r.casefold(): r for r in candidate.allowed_recipients}
        recipients = []
        for name in draft.recipients:
            match = allowed.get(name.strip().casefold())
            if match and match not in recipients:
                recipients.append(match)
            elif not match:
                adjustments.append(f"removed recipient {name!r}, who is not on the allowed list")

        follow_up = draft.follow_up
        if not follow_up and not cited:
            follow_up = True
            adjustments.append("kept: 'already resolved' was claimed without evidence")

        subject = _tidy(_CITATION.sub("", draft.subject))[:MAX_SUBJECT]
        body = _tidy(_CITATION.sub("", draft.message))
        drafted_by = DraftSource.AI
        if follow_up and (not subject or not body or len(body) > MAX_BODY):
            subject, body = template_draft(candidate, sender)
            drafted_by = DraftSource.TEMPLATE
            adjustments.append("used the template: the draft was empty or too long")

        by_number = {s.number: s for s in shown}
        outcomes.append(
            DraftOutcome(
                candidate=candidate,
                follow_up=follow_up,
                priority=ProposalPriority(draft.priority),
                rationale=rationale
                or f"Found by the {candidate.kind.value.replace('_', ' ')} check.",
                recipients=recipients,
                subject=subject,
                body=body,
                sources=[by_number[n] for n in sorted(cited)],
                drafted_by=drafted_by,
                adjustments=adjustments,
            )
        )
    return outcomes, notes
