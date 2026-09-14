"""Health endpoints.

Two distinct questions, deliberately separated:

``/health``       - "is this process alive?"  Never touches a dependency, so a
                    container orchestrator does not restart the API just
                    because the database is briefly unavailable.
``/health/deps``  - "can this process do its job?"  Checks every backing
                    service and returns 503 if any is down.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text

from app.core.config import settings
from app.core.deps import DbSession
from app.core.logging import get_logger
from app.services import dynamo
from app.services.embeddings import EmbeddingProvider, get_embedder
from app.services.job_store import JobStore, get_job_store
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider
from app.services.storage import ObjectStorage, get_storage

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
async def health_deps(
    db: DbSession,
    request: Request,
    response: Response,
    llm: Annotated[LLMProvider, Depends(get_llm_provider)],
    store: Annotated[JobStore, Depends(get_job_store)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    embedder: Annotated[EmbeddingProvider, Depends(get_embedder)],
) -> dict[str, Any]:
    pg_ok, pg_detail = await _check_postgres(db)
    ddb_ok, ddb_detail = await dynamo.check_health(store)
    llm_ok, llm_detail = await llm.health_check()
    s3_ok, s3_detail = await storage.health_check()
    emb_ok, emb_detail = embedder.health_check()

    checks = {
        "postgres": {"healthy": pg_ok, "detail": pg_detail},
        "dynamodb": {"healthy": ddb_ok, "detail": ddb_detail},
        "s3": {"healthy": s3_ok, "detail": s3_detail},
        # Keyed by provider name so the report stays accurate if it changes.
        llm.name: {"healthy": llm_ok, "detail": llm_detail},
        "embeddings": {"healthy": emb_ok, "detail": emb_detail},
    }
    all_healthy = pg_ok and ddb_ok and s3_ok and llm_ok and emb_ok

    # Only reported when this process runs the worker. A separately deployed
    # worker is monitored through its own logs, not through the API.
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        checks["worker"] = worker.health()
        all_healthy = all_healthy and checks["worker"]["healthy"]

    if not all_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if all_healthy else "degraded",
        # Visible at a glance which S3/DynamoDB this process may talk to.
        "storage_backend": settings.storage_backend.value,
        "checks": checks,
    }
