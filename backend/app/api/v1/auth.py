"""Authentication endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, DbSession
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.db.models.user import User
from app.schemas.auth import TokenResponse, UserRegisterRequest, UserResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new account",
)
async def register(payload: UserRegisterRequest, db: DbSession) -> User:
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise ConflictError("An account with this email already exists.")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
    )
    db.add(user)

    try:
        await db.commit()
    except IntegrityError as exc:
        # Two concurrent registrations can both pass the SELECT above; the
        # UNIQUE constraint is the real guarantee. Handling it here turns a
        # 500 into the correct 409.
        await db.rollback()
        raise ConflictError("An account with this email already exists.") from exc

    await db.refresh(user)
    logger.info("user registered", extra={"user_id": str(user.id)})
    return user


@router.post("/login", response_model=TokenResponse, summary="Exchange credentials for a JWT")
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> TokenResponse:
    """OAuth2 password flow.

    The form field is named ``username`` by the OAuth2 spec; we treat it as the
    email address. Using the standard form is what lets Swagger UI's Authorize
    button work directly against this endpoint.
    """
    email = form.username.strip().lower()
    user = await db.scalar(select(User).where(User.email == email))

    # Identical message and code path whether the email is unknown or the
    # password is wrong, so the response cannot be used to discover which
    # addresses have accounts.
    if user is None or not verify_password(form.password, user.password_hash):
        logger.warning("failed login attempt", extra={"email_domain": email.rpartition("@")[2]})
        raise UnauthorizedError("Incorrect email or password.")

    if not user.is_active:
        raise UnauthorizedError("This account is disabled.")

    # Transparent upgrade if the hashing parameters have been strengthened
    # since this password was last set.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(form.password)
        await db.commit()
        logger.info("password hash upgraded", extra={"user_id": str(user.id)})

    token, expires_at = create_access_token(user.id)
    logger.info("user logged in", extra={"user_id": str(user.id)})
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/me", response_model=UserResponse, summary="Current authenticated user")
async def me(current_user: CurrentUser) -> User:
    return current_user
