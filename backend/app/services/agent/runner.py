"""The follow-up agent: one bounded, audited run (M9, ADR 0015).

    ┌─ observe      detectors find overdue / due-soon / unassigned action items,
    │               stale open decisions, unresolved and recurring topics
    ├─ deduplicate  drop anything already proposed (including rejected)
    ├─ prioritise   most urgent first, at most AGENT_MAX_CANDIDATES
    ├─ recall       RAG: related passages from past meetings, per candidate
    ├─ decide+draft ONE structured model call for all candidates
    │               (model unavailable ⇒ template drafts, clearly labelled)
    ├─ verify       guardrails in code (drafting.verify_drafts)
    └─ propose      proposals saved for a person to approve or reject

Every step is appended to the run's trace, so a person can see exactly what
the agent looked at and why it suggested each follow-up. The agent never sends
anything and never changes meeting data: its only output is proposals.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.core.logging import get_logger
from app.db.models import (
    AgentRun,
    AgentRunStatus,
    FollowUpProposal,
    User,
)
from app.services.agent.detectors import KIND_LABEL, Candidate, find_candidates
from app.services.agent.drafting import AgentDrafts, DraftOutcome, template_outcome, verify_drafts
from app.services.embeddings.base import EmbeddingProvider
from app.services.llm.base import LLMError, LLMProvider
from app.services.prompts import AGENT_PROMPT_VERSION, AGENT_SYSTEM_INSTRUCTION, build_agent_prompt
from app.services.rag import Source, retrieve_context, source_header

logger = get_logger(__name__)

# A run still "running" after this long was interrupted (process restart); it
# is marked failed so it no longer blocks new runs.
STALE_RUN_AFTER = timedelta(minutes=10)


class AgentBusyError(ConflictError):
    code = "agent_run_in_progress"
    message = "The agent is already running for you. Wait for it to finish."


def _now() -> datetime:
    return datetime.now(UTC)


def source_dict(source: Source) -> dict:
    hit = source.hit
    return {
        "number": source.number,
        "meeting_id": str(hit.meeting_id),
        "meeting_title": hit.meeting_title,
        "meeting_date": hit.meeting_date.isoformat(),
        "kind": hit.source_kind.value,
        "text": source.text,
        "char_start": hit.char_start,
        "char_end": hit.char_end,
        "score": hit.score,
    }


async def _start_run(db: AsyncSession, owner_id: uuid.UUID, trigger: str) -> uuid.UUID:
    await db.execute(
        update(AgentRun)
        .where(
            AgentRun.owner_id == owner_id,
            AgentRun.status == AgentRunStatus.RUNNING,
            AgentRun.started_at < _now() - STALE_RUN_AFTER,
        )
        .values(status=AgentRunStatus.FAILED, finished_at=_now(), error_code="interrupted")
    )
    run = AgentRun(
        owner_id=owner_id,
        trigger=trigger,
        status=AgentRunStatus.RUNNING,
        prompt_version=AGENT_PROMPT_VERSION,
        steps=[],
    )
    db.add(run)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AgentBusyError() from None
    return run.id


async def run_agent(
    db: AsyncSession,
    *,
    user: User,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    today: date,
    trigger: str = "manual",
) -> AgentRun:
    owner_id, sender = user.id, user.full_name
    run_id = await _start_run(db, owner_id, trigger)
    started = time.perf_counter()
    steps: list[dict] = []

    def step(name: str, **detail) -> None:
        steps.append({"step": name, "at": _now().isoformat(), **detail})

    try:
        # -- observe ---------------------------------------------------------
        found = await find_candidates(db, owner_id=owner_id, today=today, embedder=embedder)
        by_kind: dict[str, int] = {}
        for c in found:
            by_kind[c.kind.value] = by_kind.get(c.kind.value, 0) + 1
        step("observe", tools=[
            "find_overdue_action_items", "find_due_soon_action_items",
            "find_unassigned_action_items", "find_open_decisions", "find_unresolved_topics",
        ], found=by_kind, total=len(found))  # fmt: skip

        # -- deduplicate -----------------------------------------------------
        keys = [c.dedupe_key for c in found]
        seen = (
            set(
                (
                    await db.scalars(
                        select(FollowUpProposal.dedupe_key).where(
                            FollowUpProposal.owner_id == owner_id,
                            FollowUpProposal.dedupe_key.in_(keys),
                        )
                    )
                ).all()
            )
            if keys
            else set()
        )
        fresh = [c for c in found if c.dedupe_key not in seen]
        step("deduplicate", already_proposed=len(found) - len(fresh), remaining=len(fresh))

        # -- prioritise --------------------------------------------------------
        chosen = fresh[: settings.agent_max_candidates]
        step(
            "prioritise",
            selected=[{"kind": c.kind.value, "title": c.title} for c in chosen],
            deferred=len(fresh) - len(chosen),
        )

        # -- recall ----------------------------------------------------------
        ids = {f"C{i}": c for i, c in enumerate(chosen, start=1)}
        recalled: dict[str, list[Source]] = {}
        for cid, candidate in ids.items():
            if settings.agent_context_passages:
                sources, _ = await retrieve_context(
                    db,
                    owner_id=owner_id,
                    query=candidate.query,
                    embedder=embedder,
                    top_k=settings.agent_context_passages,
                )
            else:
                sources = []
            recalled[cid] = sources
        step(
            "recall",
            tool="retrieve_context",
            passages={cid: len(s) for cid, s in recalled.items()},
        )

        # -- decide and draft --------------------------------------------------
        outcomes: list[DraftOutcome] = []
        model_name: str | None = None
        used_fallback = False
        if ids:
            prompt = build_agent_prompt(
                today=today.isoformat(),
                sender=sender,
                candidates=[
                    {
                        "id": cid,
                        "kind": KIND_LABEL[c.kind],
                        "meeting": c.meeting_label,
                        "facts": c.facts,
                        "recipients": c.allowed_recipients,
                        "sources": [(s.number, source_header(s), s.text) for s in recalled[cid]],
                    }
                    for cid, c in ids.items()
                ],
            )
            try:
                result = await llm.generate_structured(
                    system_instruction=AGENT_SYSTEM_INSTRUCTION,
                    prompt=prompt,
                    schema=AgentDrafts,
                    temperature=0.2,
                )
                model_name = result.model
                outcomes, notes = verify_drafts(result.data, ids, recalled, sender)
                step("decide_and_draft", model=result.model, drafts=len(result.data.drafts))
                step(
                    "verify",
                    notes=notes,
                    adjustments={
                        cid: o.adjustments
                        for cid, o in zip(ids, outcomes, strict=True)
                        if o.adjustments
                    },
                    resolved=[o.candidate.title for o in outcomes if not o.follow_up],
                )
            except LLMError as exc:
                used_fallback = True
                outcomes = [
                    template_outcome(c, recalled[cid], sender, "the model was unavailable")
                    for cid, c in ids.items()
                ]
                step("decide_and_draft", model=None, fallback="template", error=exc.code)

        # -- propose -----------------------------------------------------------
        proposals = [_proposal(o, owner_id, run_id) for o in outcomes if o.follow_up]
        db.add_all(proposals)
        step(
            "propose",
            created=len(proposals),
            skipped_as_resolved=sum(not o.follow_up for o in outcomes),
        )

        await db.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(
                status=AgentRunStatus.COMPLETED,
                finished_at=_now(),
                model=model_name,
                candidates_found=len(found),
                proposals_created=len(proposals),
                used_fallback=used_fallback,
                steps=steps,
            )
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("agent run failed", extra={"run_id": str(run_id)})
        step("failed", error=getattr(exc, "code", type(exc).__name__))
        await db.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(
                status=AgentRunStatus.FAILED,
                finished_at=_now(),
                steps=steps,
                error_code=getattr(exc, "code", "internal_error"),
            )
        )
        await db.commit()

    run = await db.get(AgentRun, run_id, populate_existing=True)
    logger.info(
        "agent run finished",
        extra={
            "run_id": str(run_id),
            "status": run.status.value,
            "candidates": run.candidates_found,
            "proposals": run.proposals_created,
            "fallback": run.used_fallback,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return run


def _proposal(o: DraftOutcome, owner_id: uuid.UUID, run_id: uuid.UUID) -> FollowUpProposal:
    c: Candidate = o.candidate
    return FollowUpProposal(
        owner_id=owner_id,
        run_id=run_id,
        meeting_id=c.meeting_id,
        action_item_id=c.action_item_id,
        decision_id=c.decision_id,
        kind=c.kind,
        dedupe_key=c.dedupe_key,
        priority=o.priority,
        title=c.title[:300],
        rationale=o.rationale,
        recipients=o.recipients,
        draft_subject=o.subject,
        draft_body=o.body,
        drafted_by=o.drafted_by,
        sources=[source_dict(s) for s in o.sources],
    )
