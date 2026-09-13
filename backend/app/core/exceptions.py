"""Application errors and their HTTP representation.

Service and repository code raises these domain errors; it never constructs
``HTTPException`` or knows about status codes. The handlers registered in
``app.main`` translate them into a single consistent response envelope:

    {"error": {"code": ..., "message": ..., "details": ..., "request_id": ...}}

Consistency matters for the React client in M5: one error shape to parse.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)

# Starlette has renamed this constant once already
# (HTTP_422_UNPROCESSABLE_ENTITY -> HTTP_422_UNPROCESSABLE_CONTENT) and
# referencing either spelling emits a deprecation warning on one version or
# the other. The numeric code is fixed by RFC 4918 and never changes.
HTTP_422 = 422


class AppError(Exception):
    """Base class for expected, handled failures."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self, message: str | None = None, details: Any = None, code: str | None = None
    ) -> None:
        self.message = message or self.message
        self.details = details
        # Instance-level override lets one error class carry a more specific
        # machine-readable code, e.g. ConflictError(code="transcript_missing").
        if code is not None:
            self.code = code
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "Resource already exists."


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    message = "Authentication required."


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You do not have access to this resource."


class BadGatewayError(AppError):
    """An upstream dependency answered, but the answer was unusable."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "bad_gateway"
    message = "An upstream service returned an invalid response."


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "A required dependency is unavailable."


def _envelope(code: str, message: str, request: Request, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", None),
    }
    if details is not None:
        body["details"] = details
    return {"error": body}


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning(
        "application error",
        extra={"error_code": exc.code, "status_code": exc.status_code, "path": request.url.path},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message, request, exc.details),
        headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Give FastAPI's own HTTPExceptions (404 on unknown route, etc.) the same shape."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope("http_error", str(exc.detail), request),
        headers=getattr(exc, "headers", None),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 for malformed input, with the field-level detail Pydantic produced."""
    return JSONResponse(
        status_code=HTTP_422,
        content=_envelope(
            "validation_error",
            "Request validation failed.",
            request,
            # jsonable via str(): ValueError objects inside ctx are not JSON-safe.
            details=[
                {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
                for e in exc.errors()
            ],
        ),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort.

    The real exception is logged with its traceback, but the client receives a
    generic message plus the request id - never a stack trace or SQL fragment,
    which would leak schema details to an attacker.
    """
    logger.exception("unhandled exception", extra={"path": request.url.path})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(
            "internal_error",
            "An unexpected error occurred. Quote the request_id when reporting this.",
            request,
        ),
    )
