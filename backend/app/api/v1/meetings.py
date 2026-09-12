"""Meeting CRUD.

Every endpoint here is authenticated, and every endpoint that names a specific
meeting resolves it through ``authorize_meeting_access``. No route queries the
meetings table by id directly - that is the invariant which makes the
authorization guarantee reviewable.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.db.models.meeting import Meeting, MeetingStatus
from app.schemas.common import Page
from app.schemas.meeting import (
    MeetingCreateRequest,
    MeetingResponse,
    MeetingUpdateRequest,
)
from app.services.authorization import AccessLevel, authorize_meeting_access

logger = get_logger(__name__)

router = APIRouter(prefix="/meetings", tags=["meetings"])


@router.post(
    "",
    response_model=MeetingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a meeting",
)
async def create_meeting(
    payload: MeetingCreateRequest, db: DbSession, current_user: CurrentUser
) -> Meeting:
    meeting = Meeting(
        # Ownership is taken from the token, never from the request body -
        # otherwise a client could create meetings owned by another user.
        owner_id=current_user.id,
        title=payload.title,
        description=payload.description,
        meeting_date=payload.meeting_date,
        source_type=payload.source_type,
        status=MeetingStatus.CREATED,
    )
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)

    logger.info(
        "meeting created",
        extra={"meeting_id": str(meeting.id), "user_id": str(current_user.id)},
    )
    return meeting


@router.get("", response_model=Page[MeetingResponse], summary="List my meetings")
async def list_meetings(
    db: DbSession,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: Annotated[MeetingStatus | None, Query(alias="status")] = None,
) -> Page[MeetingResponse]:
    """Only the caller's own meetings. The owner filter is not optional."""
    conditions = [Meeting.owner_id == current_user.id]
    if status_filter is not None:
        conditions.append(Meeting.status == status_filter)

    total = await db.scalar(select(func.count()).select_from(Meeting).where(*conditions)) or 0

    rows = await db.scalars(
        select(Meeting)
        .where(*conditions)
        # Matches ix_meetings_owner_id_meeting_date, so this is an index scan.
        .order_by(Meeting.meeting_date.desc())
        .offset((page - 1) * size)
        .limit(size)
    )

    return Page[MeetingResponse](
        items=[MeetingResponse.model_validate(m) for m in rows.all()],
        total=total,
        page=page,
        size=size,
    )


@router.get("/{meeting_id}", response_model=MeetingResponse, summary="Get one meeting")
async def get_meeting(meeting_id: uuid.UUID, db: DbSession, current_user: CurrentUser) -> Meeting:
    return await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)


@router.patch("/{meeting_id}", response_model=MeetingResponse, summary="Update a meeting")
async def update_meeting(
    meeting_id: uuid.UUID,
    payload: MeetingUpdateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> Meeting:
    meeting = await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.WRITE)

    # exclude_unset distinguishes "not supplied" from "explicitly set to null",
    # so an empty PATCH body changes nothing.
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(meeting, field, value)

    await db.commit()
    await db.refresh(meeting)

    logger.info(
        "meeting updated",
        extra={"meeting_id": str(meeting.id), "fields": sorted(changes)},
    )
    return meeting


@router.delete(
    "/{meeting_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a meeting",
)
async def delete_meeting(
    meeting_id: uuid.UUID, db: DbSession, current_user: CurrentUser
) -> Response:
    meeting = await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.WRITE)

    await db.delete(meeting)
    await db.commit()

    logger.info(
        "meeting deleted",
        extra={"meeting_id": str(meeting_id), "user_id": str(current_user.id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
