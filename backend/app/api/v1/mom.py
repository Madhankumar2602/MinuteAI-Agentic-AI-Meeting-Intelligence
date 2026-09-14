"""Minutes of Meeting: the structured minutes and their PDF (M7, core workflow)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, DbSession
from app.schemas.mom import MinutesOfMeeting, MomPdfResponse
from app.services.authorization import AccessLevel, authorize_meeting_access
from app.services.mom.builder import build_minutes
from app.services.mom.documents import ensure_minutes_pdf
from app.services.storage import ObjectStorage, get_storage

router = APIRouter(prefix="/meetings/{meeting_id}/mom", tags=["minutes of meeting"])

PDF_URL_TTL_SECONDS = 900


@router.get(
    "",
    response_model=MinutesOfMeeting,
    summary="The structured Minutes of Meeting",
    responses={409: {"description": "The meeting has not been processed yet (minutes_not_ready)."}},
)
async def get_minutes(
    meeting_id: uuid.UUID, db: DbSession, current_user: CurrentUser
) -> MinutesOfMeeting:
    """Assembled from the stored results, including the user's corrections, so it
    always matches what the PDF shows."""
    await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)
    return await build_minutes(db, meeting_id)


@router.post(
    "/pdf",
    response_model=MomPdfResponse,
    summary="Generate (or reuse) the Minutes of Meeting PDF and get view/download links",
    responses={409: {"description": "The meeting has not been processed yet (minutes_not_ready)."}},
)
async def minutes_pdf(
    meeting_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> MomPdfResponse:
    """The PDF is stored in object storage. An identical stored PDF is reused;
    if the minutes changed since it was made, a new one is rendered first.

    Links are short-lived presigned URLs: ``view_url`` opens in the browser,
    ``download_url`` saves the file.
    """
    await authorize_meeting_access(db, meeting_id, current_user, AccessLevel.READ)
    document = await ensure_minutes_pdf(db, meeting_id=meeting_id, storage=storage)
    return MomPdfResponse(
        filename=document.filename,
        size_bytes=document.size_bytes,
        pages=document.pages,
        fingerprint=document.fingerprint,
        reused=document.reused,
        view_url=storage.presigned_download(
            key=document.key,
            expires_in=PDF_URL_TTL_SECONDS,
            filename=document.filename,
            inline=True,
        ),
        download_url=storage.presigned_download(
            key=document.key,
            expires_in=PDF_URL_TTL_SECONDS,
            filename=document.filename,
            inline=False,
        ),
        expires_in=PDF_URL_TTL_SECONDS,
    )
