"""FastAPI application factory and entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import health
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import (
    AppError,
    app_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestIDMiddleware
from app.db.session import engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    logger.info(
        "application starting",
        extra={"environment": settings.app_env, "port": settings.app_port},
    )

    from app.services.job_store import get_job_store
    from app.workers.processing import build_worker

    if settings.dynamodb_auto_create_tables:
        try:
            await get_job_store().ensure_table()
        except Exception:
            # Start anyway: CRUD endpoints work without DynamoDB, and
            # /health/deps will report the problem clearly.
            logger.exception("could not ensure the DynamoDB jobs table")

    if settings.s3_auto_create_bucket:
        from app.services.storage import get_storage

        try:
            await get_storage().ensure_bucket(cors_origins=settings.cors_origin_list)
        except Exception:
            logger.exception("could not ensure the S3 bucket")

    stop = asyncio.Event()
    worker_task: asyncio.Task[None] | None = None
    if settings.worker_embedded:
        worker = build_worker()
        app.state.worker = worker
        worker_task = asyncio.create_task(worker.run_forever(stop), name="processing-worker")

    yield

    stop.set()
    if worker_task is not None:
        await worker_task
    # Closing the pool on shutdown avoids asyncpg warning about connections
    # garbage-collected while still open.
    await engine.dispose()
    logger.info("application stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=(
            "AI meeting intelligence: transcription, summarisation, decision and "
            "action-item extraction, cross-meeting RAG, and agentic follow-up."
        ),
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs are disabled in production: they describe every
        # endpoint and schema, which is free reconnaissance for an attacker.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Middleware order matters. Starlette runs these outermost-first, so
    # RequestIDMiddleware being added last means it wraps CORS and therefore
    # sees - and logs - every request including CORS preflights.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIDMiddleware)

    # One consistent error envelope for every failure mode.
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # Health lives at the root, outside /api/v1: probes should not have to
    # change when the API version does.
    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
