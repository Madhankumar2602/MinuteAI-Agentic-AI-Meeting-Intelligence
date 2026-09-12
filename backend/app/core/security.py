"""Password hashing and JWT issuance/verification.

Argon2id is used rather than bcrypt: it is the Password Hashing Competition
winner and the current OWASP recommendation, it is memory-hard (which raises
the cost of GPU cracking), and ``argon2-cffi`` is actively maintained - unlike
``passlib``, whose bcrypt backend additionally misdetects the version of
bcrypt 4.x and emits errors on Windows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings
from app.core.exceptions import UnauthorizedError

# Parameters are argon2-cffi's defaults, which track the RFC 9106 guidance.
# Explicit rather than implicit so the cost is visible and tunable.
_hasher = PasswordHasher(
    time_cost=3,  # iterations
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

TOKEN_TYPE_ACCESS = "access"


def hash_password(password: str) -> str:
    """Return an Argon2id hash. The salt is generated per call and embedded."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time-ish verification that never raises on bad input.

    Returns False for a wrong password, a corrupt hash, or a hash produced by
    a different algorithm - the caller only ever needs the boolean.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than we now use.

    Lets the login path transparently upgrade old hashes.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def create_access_token(
    subject: str | uuid.UUID, expires_minutes: int | None = None
) -> tuple[str, datetime]:
    """Issue a signed JWT for ``subject`` (the user id).

    Returns the token and its absolute expiry so the caller can report it
    without decoding the token again.
    """
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=expires_minutes or settings.jwt_expire_minutes)

    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid.uuid4()),  # unique id, enables future revocation lists
        "type": TOKEN_TYPE_ACCESS,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify signature and expiry, returning the claims.

    Raises UnauthorizedError - never leaks the underlying PyJWT message, which
    can distinguish "expired" from "bad signature" and helps an attacker.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Could not validate credentials.") from exc

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise UnauthorizedError("Could not validate credentials.")

    return payload
