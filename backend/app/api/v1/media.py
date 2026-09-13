"""Meeting recording upload (M4, ADR 0009).

Two-step direct-to-storage upload:

    1. POST /meetings/{id}/media/upload-url   → presigned POST + signed upload token
       (browser) POST file → storage          ← storage enforces key, type, size
    2. POST /meetings/{id}/media/complete     → verify object, sniff bytes,
                                                 record media, queue processing

The upload token is a short-lived JWT naming exactly which meeting, user, key,
and content type the URL was issued for. ``complete`` accepts nothing else, so a
client cannot confirm an object under another user's prefix, and no database
row exists until an upload is verified.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import create_media_upload_token, decode_media_upload_token
from app.db.models import (
    MeetingMedia,
    MeetingSourceType,
    MeetingStatus,
    Transcript,
    TranscriptSource,
)
from app.schemas.jobs import JobResponse
from app.schemas.media import (
    MediaResponse,
    UploadCompleteRequest,
    UploadCompleteResponse,
    UploadUrlRequest,
    UploadUrlResponse,
)
from app.services.authorization import AccessLevel, authorize_meeting_access
from app.services.job_store import JobStore, get_job_store
from app.services.media_validation import (
    SNIFF_BYTES,
    content_matches,
    resolve_format,
    safe_display_name,
)
from app.services.storage import ObjectStorage, get_storage, media_key

logger = get_logger(__name__)

router = APIRouter(prefix="/meetings/{meeting_id}/media", tags=["meeting recording"])

Storage = Annotated[ObjectStorage, Depends(get_storage)]


class UnprocessableMediaError(AppError):
    status_code = 422
    code = "media_invalid"
    message = "The uploaded file is not a supported audio or video recording."


def _ensure_not_busy(meeting_status: MeetingStatus) -> None:
    if meeting_status in (MeetingStatus.QUEUED, MeetingStatus.PROCESSING):
        raise ConflictError(
            "The recording cannot change while the meeting is queued or being processed.",
            code="processing_in_progress",
        )


@router.post("/upload-url", response_model=UploadUrlResponse, summary="Get a direct upload URL")
async def create_upload_url(
    meeting_id: uuid.UUID,
    payload: UploadUrlRequest,
    db: DbSession,
    current_user: CurrentUser,
    storage: Storage,
) -> UploadUrlResponse:
    meeting = await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.WRITE)
    _ensure_not_busy(meeting.status)

    fmt = resolve_format(payload.content_type)
    if fmt is None:
        raise UnprocessableMediaError(
            f"Unsupported file type '{payload.content_type}'. Upload an audio or video recording.",
            code="media_type_unsupported",
        )
    if payload.size_bytes > settings.media_max_bytes:
        raise UnprocessableMediaError(
            f"The file is larger than the {settings.media_max_bytes // (1024 * 1024)} MB limit.",
            code="media_too_large",
        )

    media_id = uuid.uuid4()
    key = media_key(
        user_id=current_user.id, meeting_id=meeting_id, media_id=media_id, extension=fmt.extension
    )
    upload = storage.presigned_upload(
        key=key,
        content_type=fmt.mime_type,
        max_bytes=settings.media_max_bytes,
        expires_in=settings.media_upload_url_ttl_seconds,
    )
    token = create_media_upload_token(
        user_id=current_user.id,
        meeting_id=meeting_id,
        media_id=media_id,
        key=key,
        content_type=fmt.mime_type,
        filename=safe_display_name(payload.filename),
        expires_seconds=settings.media_upload_url_ttl_seconds,
    )
    logger.info(
        "upload url issued",
        extra={
            "meeting_id": str(meeting_id),
            "media_id": str(media_id),
            "content_type": fmt.mime_type,
        },
    )
    return UploadUrlResponse(
        upload_url=upload.url,
        fields=upload.fields,
        upload_token=token,
        expires_in=upload.expires_in,
        max_bytes=settings.media_max_bytes,
    )


@router.post(
    "/complete",
    response_model=UploadCompleteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Confirm an upload and queue transcription + processing",
)
async def complete_upload(
    meeting_id: uuid.UUID,
    payload: UploadCompleteRequest,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    storage: Storage,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> UploadCompleteResponse:
    claims = decode_media_upload_token(payload.upload_token)
    # The token must have been issued to this user, for this meeting.
    if claims.user_id != current_user.id or claims.meeting_id != meeting_id:
        raise NotFoundError("Upload not found.")

    meeting = await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.WRITE)
    _ensure_not_busy(meeting.status)

    transcript = await db.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
    if transcript is not None and transcript.source == TranscriptSource.MANUAL:
        if not payload.replace_manual_transcript:
            raise ConflictError(
                "This meeting already has a typed transcript, which takes precedence over a "
                "recording. Resend with replace_manual_transcript=true to transcribe the recording instead.",
                code="manual_transcript_exists",
            )

    stored = await storage.head(claims.key)
    if stored is None:
        raise ConflictError("The file has not been uploaded yet.", code="upload_not_found")

    fmt = resolve_format(claims.content_type)
    head = await storage.read_prefix_bytes(claims.key, SNIFF_BYTES)
    problem = None
    if stored.size_bytes > settings.media_max_bytes:
        problem = "The file is larger than the upload limit."
    elif fmt is None or (stored.content_type or "").lower() != fmt.mime_type:
        problem = "The stored file type does not match the upload."
    elif not content_matches(fmt, head):
        problem = f"The file contents are not a valid {fmt.extension.upper()} recording."
    if problem:
        # Never keep bytes that failed validation.
        await storage.delete(claims.key)
        logger.warning(
            "upload rejected",
            extra={
                "meeting_id": str(meeting_id),
                "media_id": str(claims.media_id),
                "reason": problem,
            },
        )
        raise UnprocessableMediaError(problem)

    existing = await db.scalar(select(MeetingMedia).where(MeetingMedia.meeting_id == meeting_id))
    previous_key = existing.s3_key if existing is not None else None
    if existing is not None:
        await db.delete(existing)
        await db.flush()
    media = MeetingMedia(
        id=claims.media_id,
        meeting_id=meeting_id,
        s3_key=claims.key,
        content_type=fmt.mime_type,
        size_bytes=stored.size_bytes,
        etag=stored.etag,
        original_filename=claims.filename,
    )
    db.add(media)
    if transcript is not None and transcript.source == TranscriptSource.MANUAL:
        await db.execute(delete(Transcript).where(Transcript.id == transcript.id))
    meeting.source_type = (
        MeetingSourceType.VIDEO if fmt.kind == "video" else MeetingSourceType.AUDIO
    )
    await db.commit()
    await db.refresh(media)

    if previous_key and previous_key != claims.key:
        try:
            await storage.delete(previous_key)
        except Exception:  # noqa: BLE001 - an orphaned object must not fail the upload
            logger.warning(
                "could not delete replaced recording", extra={"key_suffix": previous_key[-48:]}
            )

    job, created = await store.create_job(
        meeting_id=meeting_id, owner_id=current_user.id, force=False
    )
    if created:
        meeting.status = MeetingStatus.QUEUED
        await db.commit()
        worker = getattr(request.app.state, "worker", None)
        if worker is not None:
            worker.notify()

    logger.info(
        "upload confirmed",
        extra={
            "meeting_id": str(meeting_id),
            "media_id": str(media.id),
            "size_bytes": stored.size_bytes,
            "job_id": job.job_id,
        },
    )
    return UploadCompleteResponse(
        media=MediaResponse.model_validate(media), job=JobResponse.from_record(job)
    )


@router.get("", response_model=MediaResponse, summary="Get the recording, with a playback URL")
async def get_media(
    meeting_id: uuid.UUID, db: DbSession, current_user: CurrentUser, storage: Storage
) -> MediaResponse:
    await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)
    media = await db.scalar(select(MeetingMedia).where(MeetingMedia.meeting_id == meeting_id))
    if media is None:
        raise NotFoundError("This meeting has no recording.")
    response = MediaResponse.model_validate(media)
    response.download_url = storage.presigned_download(key=media.s3_key, expires_in=900)
    return response


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Delete the recording")
async def delete_media(
    meeting_id: uuid.UUID, db: DbSession, current_user: CurrentUser, storage: Storage
) -> Response:
    """Removes the recording. A transcript already produced from it is kept."""
    meeting = await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.WRITE)
    _ensure_not_busy(meeting.status)
    media = await db.scalar(select(MeetingMedia).where(MeetingMedia.meeting_id == meeting_id))
    if media is None:
        raise NotFoundError("This meeting has no recording.")
    key = media.s3_key
    await db.delete(media)
    await db.commit()
    await storage.delete(key)
    logger.info("recording deleted", extra={"meeting_id": str(meeting_id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
