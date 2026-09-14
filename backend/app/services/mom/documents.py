"""Stored MOM PDFs: render once per distinct content, keep only the latest.

    minutes (JSON) + renderer version ──sha256──► fingerprint
    key = users/{user}/meetings/{meeting}/mom/{fingerprint[:32]}.pdf

* Same minutes → same key → the stored PDF is reused (no re-render).
* Minutes change (re-processing, an action item marked done, a decision
  resolved) → new fingerprint → a new PDF; older PDFs of the meeting are deleted.
* The key sits under the meeting prefix, so deleting the meeting deletes its
  PDFs with the rest of its objects.

The fingerprint hashes the minutes, not the PDF bytes: ReportLab embeds a
creation time, so identical minutes would otherwise never match.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Meeting
from app.schemas.mom import MinutesOfMeeting
from app.services.mom.builder import build_minutes
from app.services.mom.pdf import RENDERER_VERSION, render_minutes_pdf
from app.services.storage import ObjectStorage, mom_prefix

logger = get_logger(__name__)

PDF_CONTENT_TYPE = "application/pdf"


@dataclass(frozen=True, slots=True)
class MomDocument:
    key: str
    filename: str
    fingerprint: str
    size_bytes: int
    pages: int | None
    reused: bool


def minutes_fingerprint(minutes: MinutesOfMeeting) -> str:
    payload = RENDERER_VERSION + "\n" + minutes.model_dump_json()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def pdf_filename(minutes: MinutesOfMeeting) -> str:
    """ASCII-safe, human-readable download name, e.g. ``MOM - Platform sync - 2026-09-14.pdf``."""
    title = re.sub(r"[^A-Za-z0-9 ._-]+", " ", minutes.title)
    title = " ".join(title.split())[:80].strip(" .") or "Meeting"
    return f"MOM - {title} - {minutes.meeting_date.date().isoformat()}.pdf"


async def ensure_minutes_pdf(
    db: AsyncSession, *, meeting_id: uuid.UUID, storage: ObjectStorage
) -> MomDocument:
    minutes = await build_minutes(db, meeting_id)
    owner_id = await db.scalar(select(Meeting.owner_id).where(Meeting.id == meeting_id))
    fingerprint = minutes_fingerprint(minutes)
    prefix = mom_prefix(user_id=owner_id, meeting_id=meeting_id)
    key = f"{prefix}{fingerprint[:32]}.pdf"
    filename = pdf_filename(minutes)

    existing = await storage.head(key)
    if existing is not None:
        return MomDocument(key, filename, fingerprint, existing.size_bytes, None, reused=True)

    # Rendering is CPU work (tens of milliseconds); keep it off the event loop.
    data, pages = await asyncio.to_thread(render_minutes_pdf, minutes)
    await storage.put_bytes(key, data, content_type=PDF_CONTENT_TYPE)
    # Only the current minutes are kept. Deleted after the upload succeeds, so a
    # failure never leaves the meeting without a PDF.
    for old in await storage.list_keys(prefix):
        if old != key:
            await storage.delete(old)

    logger.info(
        "minutes pdf generated",
        extra={"meeting_id": str(meeting_id), "bytes": len(data), "pages": pages},
    )
    return MomDocument(key, filename, fingerprint, len(data), pages, reused=False)
