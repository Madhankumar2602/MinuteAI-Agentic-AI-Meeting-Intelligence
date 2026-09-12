"""Reusable FastAPI dependencies."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import UnauthorizedError
from app.core.security import decode_access_token
from app.db.models.user import User
from app.db.session import get_db

# tokenUrl is what makes Swagger UI's "Authorize" button work: it tells the
# docs page where to POST credentials to obtain a token.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbSession,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    """Resolve the bearer token to a live, active user row.

    The token is only a claim about identity; it is checked against the
    database on every request so that deleting or deactivating an account takes
    effect immediately rather than when the token happens to expire.
    """
    payload = decode_access_token(token)

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("Could not validate credentials.") from exc

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise UnauthorizedError("Could not validate credentials.")
    if not user.is_active:
        raise UnauthorizedError("This account is disabled.")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
