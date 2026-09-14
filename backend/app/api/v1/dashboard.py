"""Dashboard summary: everything the home screen needs in one request (M5).

Aggregation happens in SQL (COUNT ... GROUP BY / FILTER) rather than by fetching
rows and counting in Python, so the cost stays flat as a user's history grows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.db.models import OPEN_ACTION_STATUSES, ActionItem, ActionItemStatus, Meeting, MeetingStatus
from app.schemas.intelligence import ActionItemResponse
from app.schemas.meeting import MeetingResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

DUE_SOON_DAYS = 7


class MeetingCounts(BaseModel):
    total: int
    by_status: dict[MeetingStatus, int]


class ActionItemCounts(BaseModel):
    open: int
    overdue: int
    due_soon: int
    done: int


class DashboardResponse(BaseModel):
    meetings: MeetingCounts
    action_items: ActionItemCounts
    recent_meetings: list[MeetingResponse]
    attention: list[ActionItemResponse]


@router.get(
    "",
    response_model=DashboardResponse,
    summary="Counts, recent meetings, and items needing attention",
)
async def get_dashboard(db: DbSession, current_user: CurrentUser) -> DashboardResponse:
    today = datetime.now(UTC).date()
    soon = today + timedelta(days=DUE_SOON_DAYS)

    status_rows = await db.execute(
        select(Meeting.status, func.count())
        .where(Meeting.owner_id == current_user.id)
        .group_by(Meeting.status)
    )
    by_status = {s: 0 for s in MeetingStatus}
    for meeting_status, count in status_rows.all():
        by_status[meeting_status] = count

    is_open = ActionItem.status.in_(OPEN_ACTION_STATUSES)
    counts = (
        await db.execute(
            select(
                func.count().filter(is_open),
                func.count().filter(is_open, ActionItem.deadline < today),
                func.count().filter(
                    is_open, ActionItem.deadline >= today, ActionItem.deadline <= soon
                ),
                func.count().filter(ActionItem.status == ActionItemStatus.DONE),
            )
            .select_from(ActionItem)
            .join(Meeting, Meeting.id == ActionItem.meeting_id)
            .where(Meeting.owner_id == current_user.id)
        )
    ).one()

    recent = await db.scalars(
        select(Meeting)
        .where(Meeting.owner_id == current_user.id)
        .order_by(Meeting.meeting_date.desc())
        .limit(5)
    )

    # Open items that are overdue or due within the week, most urgent first.
    attention_rows = await db.execute(
        select(ActionItem, Meeting.title)
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .where(Meeting.owner_id == current_user.id, is_open, ActionItem.deadline <= soon)
        .order_by(ActionItem.deadline.asc(), ActionItem.position)
        .limit(8)
    )

    return DashboardResponse(
        meetings=MeetingCounts(total=sum(by_status.values()), by_status=by_status),
        action_items=ActionItemCounts(
            open=counts[0], overdue=counts[1], due_soon=counts[2], done=counts[3]
        ),
        recent_meetings=[MeetingResponse.model_validate(m) for m in recent.all()],
        attention=[
            ActionItemResponse.model_validate(item).model_copy(update={"meeting_title": title})
            for item, title in attention_rows.all()
        ],
    )
