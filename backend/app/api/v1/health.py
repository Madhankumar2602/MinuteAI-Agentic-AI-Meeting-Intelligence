"""Health endpoints.

Two distinct questions, deliberately separated:

``/health``       - "is this process alive?"  Never touches a dependency, so a
                    container orchestrator does not restart the API just
                    because the database is briefly unavailable.
``/health/deps``  - "can this process do its job?"  Checks every backing
                    service and returns 503 if any is down.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.config import settings
from app.core.deps import DbSession
from app.core.logging import get_logger
from app.services import dynamo

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.app_env,
        "version": "0.1.0",
    }


async def _check_postgres(db: DbSession) -> tuple[bool, str]:
    try:
        await db.execute(text("SELECT 1"))
        # Confirms the extension M6 depends on is still present, not just that
        # the connection works.
        has_vector = await db.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        )
        return True, f"reachable (pgvector={'yes' if has_vector else 'NO'})"
    except Exception as exc:  # noqa: BLE001
        logger.warning("postgres health check failed", extra={"error": str(exc)})
        return False, type(exc).__name__


@router.get("/health/deps", summary="Readiness probe - checks backing services")
async def health_deps(db: DbSession, response: Response) -> dict[str, Any]:
    pg_ok, pg_detail = await _check_postgres(db)
    ddb_ok, ddb_detail = await dynamo.check_health()

    checks = {
        "postgres": {"healthy": pg_ok, "detail": pg_detail},
        "dynamodb": {"healthy": ddb_ok, "detail": ddb_detail},
    }
    all_healthy = pg_ok and ddb_ok

    if not all_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ok" if all_healthy else "degraded", "checks": checks}
