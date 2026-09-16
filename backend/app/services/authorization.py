"""Central authorization for meeting-scoped resources.

Every route that touches a meeting goes through
``authorize_meeting_access``. Concentrating the rule in one function is what
makes it auditable: there is exactly one place to read to know who can see
what, and exactly one place to change if the rule ever changes.

Access is by ownership (ADR 0004). This function is the single extension point:
any other rule would change this file and nothing else.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models.action_item import ActionItem
from app.db.models.decision import Decision
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


async def authorize_action_item_access(
    db: AsyncSession,
    action_item_id: uuid.UUID,
    user: User,
    level: AccessLevel = AccessLevel.READ,
) -> ActionItem:
    """Authorise via the owning meeting - there is no separate rule for items.

    An action item is reachable exactly when its meeting is. Delegating keeps
    the access rule in one function (``authorize_meeting_access``), so sharing
    added there later automatically applies to action items too.
    """
    item = await db.scalar(select(ActionItem).where(ActionItem.id == action_item_id))
    if item is None:
        raise NotFoundError("Action item not found.")
    try:
        await authorize_meeting_access(db, item.meeting_id, user, level)
    except NotFoundError:
        # Same 404-not-403 reasoning: do not confirm the item id exists.
        raise NotFoundError("Action item not found.") from None
    return item


async def authorize_decision_access(
    db: AsyncSession,
    decision_id: uuid.UUID,
    user: User,
    level: AccessLevel = AccessLevel.READ,
) -> Decision:
    decision = await db.scalar(select(Decision).where(Decision.id == decision_id))
    if decision is None:
        raise NotFoundError("Decision not found.")
    try:
        await authorize_meeting_access(db, decision.meeting_id, user, level)
    except NotFoundError:
        raise NotFoundError("Decision not found.") from None
    return decision
