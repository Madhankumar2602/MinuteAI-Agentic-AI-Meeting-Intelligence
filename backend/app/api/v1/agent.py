"""Follow-up agent: run it, review its proposals, approve or reject (M9)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.db.models import AgentRun, FollowUpProposal, Meeting, ProposalStatus
from app.schemas.agent import (
    AgentRunResponse,
    ApproveRequest,
    ProposalList,
    ProposalResponse,
    RejectRequest,
)
from app.services.agent.runner import run_agent
from app.services.embeddings import EmbeddingProvider, get_embedder
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider

logger = get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["follow-up agent"])


def get_today() -> date:
    """The agent's notion of "today" (a dependency so tests can fix it)."""
    return datetime.now(UTC).date()


@router.post(
    "/runs",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run the follow-up agent over your meetings now",
    responses={409: {"description": "A run is already in progress (agent_run_in_progress)."}},
)
async def start_run(
    db: DbSession,
    current_user: CurrentUser,
    llm: Annotated[LLMProvider, Depends(get_llm_provider)],
    embedder: Annotated[EmbeddingProvider, Depends(get_embedder)],
    today: Annotated[date, Depends(get_today)],
) -> AgentRun:
    """Reviews your meetings for overdue and upcoming work, open decisions, and
    unresolved or recurring topics, and drafts follow-ups for you to approve.
    Nothing is sent. The response includes the full step-by-step trace."""
    return await run_agent(db, user=current_user, llm=llm, embedder=embedder, today=today)


@router.get("/runs", response_model=list[AgentRunResponse], summary="Recent agent runs")
async def list_runs(
    db: DbSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[AgentRun]:
    rows = await db.scalars(
        select(AgentRun)
        .where(AgentRun.owner_id == current_user.id)
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
    )
    return list(rows.all())


def _response(proposal: FollowUpProposal, title: str, meeting_date: datetime) -> ProposalResponse:
    return ProposalResponse.model_validate(
        {
            **{c.key: getattr(proposal, c.key) for c in FollowUpProposal.__table__.columns},
            "meeting_title": title,
            "meeting_date": meeting_date,
        }
    )


@router.get("/proposals", response_model=ProposalList, summary="Follow-ups proposed by the agent")
async def list_proposals(
    db: DbSession,
    current_user: CurrentUser,
    status_filter: Annotated[ProposalStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ProposalList:
    stmt = (
        select(FollowUpProposal, Meeting.title, Meeting.meeting_date)
        .join(Meeting, Meeting.id == FollowUpProposal.meeting_id)
        .where(FollowUpProposal.owner_id == current_user.id)
        .order_by(FollowUpProposal.created_at.desc())
        .limit(limit)
    )
    if status_filter is not None:
        stmt = stmt.where(FollowUpProposal.status == status_filter)
    rows = (await db.execute(stmt)).all()
    pending = await db.scalar(
        select(func.count()).where(
            FollowUpProposal.owner_id == current_user.id,
            FollowUpProposal.status == ProposalStatus.PROPOSED,
        )
    )
    return ProposalList(items=[_response(p, t, d) for p, t, d in rows], pending=pending or 0)


async def _own_proposal(
    db: DbSession, proposal_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[FollowUpProposal, str, datetime]:
    row = (
        await db.execute(
            select(FollowUpProposal, Meeting.title, Meeting.meeting_date)
            .join(Meeting, Meeting.id == FollowUpProposal.meeting_id)
            .where(FollowUpProposal.id == proposal_id, FollowUpProposal.owner_id == user_id)
        )
    ).one_or_none()
    # Someone else's proposal and a missing one look the same (404, ADR 0004).
    if row is None:
        raise NotFoundError("Proposal not found.")
    proposal, title, meeting_date = row
    if proposal.status != ProposalStatus.PROPOSED:
        raise ConflictError(
            f"This follow-up was already {proposal.status.value}.", code="proposal_already_decided"
        )
    return proposal, title, meeting_date


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=ProposalResponse,
    summary="Approve a follow-up, optionally with your edits",
)
async def approve(
    proposal_id: uuid.UUID, payload: ApproveRequest, db: DbSession, current_user: CurrentUser
) -> ProposalResponse:
    """Records your approval and the final wording. MinuteAI does not send it:
    the approved message is ready for you to send from your own email or chat."""
    proposal, title, meeting_date = await _own_proposal(db, proposal_id, current_user.id)
    proposal.status = ProposalStatus.APPROVED
    proposal.final_subject = (payload.subject or proposal.draft_subject).strip()
    proposal.final_body = (payload.body or proposal.draft_body).strip()
    proposal.decision_note = payload.note
    proposal.decided_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(proposal)
    logger.info(
        "follow-up approved",
        extra={"proposal_id": str(proposal_id), "edited": bool(payload.subject or payload.body)},
    )
    return _response(proposal, title, meeting_date)


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=ProposalResponse,
    summary="Reject a follow-up; it will not be proposed again",
)
async def reject(
    proposal_id: uuid.UUID, payload: RejectRequest, db: DbSession, current_user: CurrentUser
) -> ProposalResponse:
    proposal, title, meeting_date = await _own_proposal(db, proposal_id, current_user.id)
    proposal.status = ProposalStatus.REJECTED
    proposal.decision_note = payload.note
    proposal.decided_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(proposal)
    logger.info("follow-up rejected", extra={"proposal_id": str(proposal_id)})
    return _response(proposal, title, meeting_date)
