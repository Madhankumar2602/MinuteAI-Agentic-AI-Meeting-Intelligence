"""Central authorization for meeting-scoped resources.

Every route that touches a meeting - now, and in M2 through M9 - goes through
``authorize_meeting_access``. Concentrating the rule in one function is what
makes it auditable: there is exactly one place to read to know who can see
what, and exactly one place to change when sharing arrives.

Sharing (the ``meeting_shares`` table) is deliberately NOT implemented yet.
It remains in the architecture and in ADR 0004; this function is the extension
point, so adding it later changes this file and nothing else.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models.meeting import Meeting
from app.db.models.user import User

logger = get_logger(__name__)


class AccessLevel(StrEnum):
    """Required level of access.

    Both map to ownership today. The distinction exists so that call sites
    already declare their intent, and a future `viewer` share grants READ
    without silently granting WRITE.
    """

    READ = "read"
    WRITE = "write"


async def authorize_meeting_access(
    db: AsyncSession,
    meeting_id: uuid.UUID,
    user: User,
    level: AccessLevel = AccessLevel.READ,
) -> Meeting:
    """Return the meeting if ``user`` may access it, else raise NotFoundError.

    Security note - why 404 and not 403:
      Returning 403 for someone else's meeting confirms that the id exists.
      An attacker could enumerate valid meeting ids by watching for 403 rather
      than 404. Both "no such meeting" and "not your meeting" therefore return
      an identical 404, so the response reveals nothing.
    """
    meeting = await db.scalar(select(Meeting).where(Meeting.id == meeting_id))

    if meeting is None:
        raise NotFoundError("Meeting not found.")

    # --- ownership rule (the only rule in M1) ---------------------------
    if meeting.owner_id != user.id:
        # Logged at WARNING: repeated entries are a genuine signal of probing.
        logger.warning(
            "meeting access denied",
            extra={
                "meeting_id": str(meeting_id),
                "user_id": str(user.id),
                "required_level": level.value,
            },
        )
        raise NotFoundError("Meeting not found.")

    return meeting
