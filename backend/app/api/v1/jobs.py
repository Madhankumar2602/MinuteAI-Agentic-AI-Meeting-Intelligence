"""Processing job status endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUser, DbSession
from app.core.exceptions import NotFoundError
from app.schemas.jobs import JobResponse
from app.services.authorization import AccessLevel, authorize_meeting_access
from app.services.job_store import JobStore, get_job_store

router = APIRouter(tags=["processing jobs"])

Store = Annotated[JobStore, Depends(get_job_store)]


@router.get("/jobs/{job_id}", response_model=JobResponse, summary="Get a processing job")
async def get_job(
    job_id: uuid.UUID, db: DbSession, current_user: CurrentUser, store: Store
) -> JobResponse:
    """Poll this after POST /process until ``status`` is COMPLETED or FAILED."""
    job = await store.get_job(str(job_id))
    if job is None:
        raise NotFoundError("Job not found.")
    # Jobs have no access rule of their own: a job is visible exactly when its
    # meeting is. A job for someone else's (or a deleted) meeting is a 404.
    try:
        await authorize_meeting_access(
            db, uuid.UUID(job.meeting_id), current_user, AccessLevel.READ
        )
    except NotFoundError:
        raise NotFoundError("Job not found.") from None
    return JobResponse.from_record(job)


@router.get(
    "/meetings/{meeting_id}/jobs",
    response_model=list[JobResponse],
    summary="Processing history for a meeting, newest first",
)
async def list_meeting_jobs(
    meeting_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    store: Store,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[JobResponse]:
    await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)
    return [
        JobResponse.from_record(j) for j in await store.list_jobs_for_meeting(meeting_id, limit)
    ]
