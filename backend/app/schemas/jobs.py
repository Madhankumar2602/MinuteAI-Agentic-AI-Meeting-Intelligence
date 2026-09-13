"""Processing job schemas."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.db.models import MeetingStatus
from app.services.job_store import JobRecord, JobStatus


class JobError(BaseModel):
    code: str
    message: str


class JobEvent(BaseModel):
    at: str
    type: str = Field(
        description=(
            "queued, started, transcription_started, transcription_completed, "
            "retry_scheduled, lease_expired_requeued, completed, failed"
        )
    )
    detail: dict[str, Any] = Field(default_factory=dict)


class JobResult(BaseModel):
    cached: bool
    # True when this run transcribed a recording before extraction (M4).
    transcribed: bool = False
    decisions: int
    action_items: int
    participants: int
    warnings: list[str]


class JobResponse(BaseModel):
    job_id: uuid.UUID
    meeting_id: uuid.UUID
    status: JobStatus
    force: bool
    attempts: int
    max_attempts: int
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    next_attempt_at: str | None = Field(
        description="When a QUEUED job becomes runnable (later than now after a failed attempt)."
    )
    error: JobError | None = Field(
        description="For FAILED jobs, the final error. For a QUEUED job that already failed once, "
        "the error that caused the retry."
    )
    result: JobResult | None
    events: list[JobEvent]

    @classmethod
    def from_record(cls, job: JobRecord) -> JobResponse:
        return cls(
            job_id=uuid.UUID(job.job_id),
            meeting_id=uuid.UUID(job.meeting_id),
            status=job.status,
            force=job.force,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            # Only meaningful while queued; while processing the same attribute
            # holds the worker's lease expiry, which is an internal detail.
            next_attempt_at=job.available_at if job.status == JobStatus.QUEUED else None,
            error=(
                JobError(code=job.error_code, message=job.error_message or "")
                if job.error_code
                else None
            ),
            result=JobResult(**job.result) if job.result else None,
            events=[
                JobEvent(
                    at=e["at"],
                    type=e["type"],
                    detail={k: v for k, v in e.items() if k not in {"at", "type"}},
                )
                for e in job.events
            ],
        )


class ProcessSubmissionResponse(BaseModel):
    """Response to POST /meetings/{id}/process.

    * 202 with ``job`` set: work was queued (or an identical job was already
      active and is returned instead of starting a duplicate).
    * 200 with ``job`` null and ``cached`` true: stored results are already
      current for this transcript, prompt, and model. Nothing was queued.
    """

    cached: bool
    meeting_status: MeetingStatus
    job: JobResponse | None
