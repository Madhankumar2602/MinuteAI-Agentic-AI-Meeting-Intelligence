"""Action items and decisions across all of the caller's meetings."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.db.models import (
    OPEN_ACTION_STATUSES,
    ActionItem,
    ActionItemStatus,
    Decision,
    Meeting,
)
from app.schemas.common import Page
from app.schemas.intelligence import (
    ActionItemResponse,
    ActionItemUpdateRequest,
    DecisionResponse,
    DecisionUpdateRequest,
)
from app.services.authorization import (
    AccessLevel,
    authorize_action_item_access,
    authorize_decision_access,
)

logger = get_logger(__name__)

router = APIRouter(tags=["action items & decisions"])


@router.get(
    "/action-items",
    response_model=Page[ActionItemResponse],
    summary="List my action items across all meetings",
)
async def list_action_items(
    db: DbSession,
    current_user: CurrentUser,
    status: Annotated[ActionItemStatus | None, Query()] = None,
    overdue: Annotated[
        bool, Query(description="Only open items whose deadline has passed.")
    ] = False,
    meeting_id: Annotated[uuid.UUID | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[ActionItemResponse]:
    # The JOIN on meetings.owner_id IS the authorization for a list endpoint:
    # rows from other users' meetings are never selected in the first place.
    conditions = [Meeting.owner_id == current_user.id]
    if status is not None:
        conditions.append(ActionItem.status == status)
    if meeting_id is not None:
        conditions.append(ActionItem.meeting_id == meeting_id)
    if overdue:
        conditions.append(ActionItem.status.in_(OPEN_ACTION_STATUSES))
        conditions.append(ActionItem.deadline < datetime.now(UTC).date())

    base = select(ActionItem).join(Meeting, Meeting.id == ActionItem.meeting_id).where(*conditions)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await db.execute(
        base.add_columns(Meeting.title)
        # Soonest deadline first, undated items last, then meeting order.
        .order_by(
            ActionItem.deadline.asc().nulls_last(), Meeting.meeting_date.desc(), ActionItem.position
        )
        .offset((page - 1) * size)
        .limit(size)
    )
    return Page[ActionItemResponse](
        items=[
            ActionItemResponse.model_validate(item).model_copy(update={"meeting_title": title})
            for item, title in rows.all()
        ],
        total=total,
        page=page,
        size=size,
    )


@router.patch(
    "/action-items/{action_item_id}",
    response_model=ActionItemResponse,
    summary="Update or correct an action item",
)
async def update_action_item(
    action_item_id: uuid.UUID,
    payload: ActionItemUpdateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> ActionItem:
    item = await authorize_action_item_access(db, action_item_id, current_user, AccessLevel.WRITE)
    changes = payload.model_dump(exclude_unset=True)
    for field_name, value in changes.items():
        setattr(item, field_name, value)
    await db.commit()
    await db.refresh(item)
    logger.info(
        "action item updated",
        extra={"action_item_id": str(item.id), "fields": sorted(changes)},
    )
    return item


@router.patch(
    "/decisions/{decision_id}",
    response_model=DecisionResponse,
    summary="Update or correct a decision",
)
async def update_decision(
    decision_id: uuid.UUID,
    payload: DecisionUpdateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> Decision:
    decision = await authorize_decision_access(db, decision_id, current_user, AccessLevel.WRITE)
    changes = payload.model_dump(exclude_unset=True)
    for field_name, value in changes.items():
        setattr(decision, field_name, value)
    await db.commit()
    await db.refresh(decision)
    logger.info(
        "decision updated", extra={"decision_id": str(decision.id), "fields": sorted(changes)}
    )
    return decision
