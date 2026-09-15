"""Background processing worker (ADR 0008).

Lifecycle of one job:

    API POST /process ──► DynamoDB: job QUEUED + meeting lock      (Postgres: meeting QUEUED)
                                   │
    worker: claim_next ──► job PROCESSING, lease = now + JOB_LEASE_SECONDS
            heartbeat  ──► lease renewed every lease/3 while running
            [transcribe] ─► index ─► extract ─► MOM PDF            (Postgres: PROCESSING)
                                   │
          ┌──────── success ───────┼──── transient error ─────┬──── permanent error ────┐
          ▼                        │    attempts remain       ▼                          │
    job COMPLETED + lock released  │    job QUEUED, retry at  job FAILED + lock released │
    (Postgres: COMPLETED)          │    now + base*4^(n-1)    (Postgres: FAILED)         │
                                   │    (Postgres: QUEUED)                               │
                                   ▼
    worker process dies mid-job ─► lease lapses ─► recover_stale_jobs() on any worker:
                                   requeue (attempts remain) or FAILED

The worker runs embedded in the API process by default, or standalone:

    python -m app.workers.processing
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger
from app.db.models import Meeting, MeetingStatus
from app.services.embeddings.base import EmbeddingProvider, EmbeddingUnavailableError
from app.services.embeddings.indexing import index_meeting, index_minutes
from app.services.intelligence import (
    is_result_current,
    result_counts,
    run_extraction,
    set_meeting_status,
)
from app.services.job_store import JobRecord, JobStore, JobStoreUnavailableError
from app.services.llm.base import (
    LLMProvider,
    LLMRateLimitError,
    LLMUnavailableError,
    TranscriptionProvider,
)
from app.services.mom.documents import ensure_minutes_pdf
from app.services.storage import ObjectStorage, StorageUnavailableError
from app.services.transcription import media_needing_transcription, transcribe_meeting

logger = get_logger(__name__)

# Failures worth another attempt later: the provider or a store was briefly
# unavailable. Everything else (bad key, invalid model output after a re-roll,
# missing transcript, deleted meeting, bugs) fails immediately - retrying would
# spend quota to reproduce the same error.
RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    LLMRateLimitError,
    LLMUnavailableError,
    JobStoreUnavailableError,
    StorageUnavailableError,
    EmbeddingUnavailableError,
    OperationalError,
)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def classify_error(exc: BaseException) -> tuple[str, str, bool]:
    """Return (error_code, safe_message, retryable).

    Messages come only from AppError subclasses, which are written for users.
    Anything else is summarised generically: raw exception text can contain SQL,
    file paths, or provider internals and is kept in the logs instead.
    """
    retryable = isinstance(exc, RETRYABLE_ERRORS)
    if isinstance(exc, AppError):
        return exc.code, exc.message, retryable
    if isinstance(exc, DBAPIError):
        return "database_error", "A database error interrupted processing.", retryable
    return "internal_error", "An unexpected error interrupted processing.", False


class ProcessingWorker:
    def __init__(
        self,
        *,
        store: JobStore,
        session_factory: SessionFactory,
        llm_factory: Callable[[], LLMProvider],
        storage: ObjectStorage,
        transcriber_factory: Callable[[], TranscriptionProvider],
        embedder_factory: Callable[[], EmbeddingProvider],
        concurrency: int = 2,
        poll_seconds: float = 2.0,
        lease_seconds: int = 120,
        retry_base_seconds: int = 30,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self._session_factory = session_factory
        self._llm_factory = llm_factory
        self.storage = storage
        self._transcriber_factory = transcriber_factory
        self._embedder_factory = embedder_factory
        self._concurrency = concurrency
        self._poll_seconds = poll_seconds
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        # host:pid:random - identifies the owner in job events and logs.
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"

        self._wake = asyncio.Event()
        self._active: set[asyncio.Task[None]] = set()
        self.last_poll_at: datetime | None = None
        self.jobs_completed = 0
        self.jobs_failed = 0
        self.jobs_retried = 0

    # ------------------------------------------------------------------
    # Driving the worker
    # ------------------------------------------------------------------

    def notify(self) -> None:
        """Wake the loop now instead of at the next poll (called after enqueue)."""
        self._wake.set()

    async def run_once(self) -> int:
        """Recover stale jobs, then run every currently runnable job to completion.

        Sequential and deterministic, for tests and one-shot invocations.
        Returns the number of jobs processed.
        """
        await self._recover_stale()
        processed = 0
        while (
            job := await self.store.claim_next(
                worker_id=self.worker_id, lease_seconds=self._lease_seconds
            )
        ) is not None:
            await self._process(job)
            processed += 1
        return processed

    async def run_forever(self, stop: asyncio.Event) -> None:
        logger.info(
            "worker started",
            extra={"worker_id": self.worker_id, "concurrency": self._concurrency},
        )
        while not stop.is_set():
            try:
                await self._recover_stale()
                while len(self._active) < self._concurrency:
                    job = await self.store.claim_next(
                        worker_id=self.worker_id, lease_seconds=self._lease_seconds
                    )
                    if job is None:
                        break
                    task = asyncio.create_task(self._process(job), name=f"job-{job.job_id}")
                    self._active.add(task)
                    task.add_done_callback(self._active.discard)
            except Exception:
                # A DynamoDB or database outage must not kill the loop; it
                # retries at the next poll.
                logger.exception(
                    "worker loop iteration failed", extra={"worker_id": self.worker_id}
                )
            self.last_poll_at = datetime.now(UTC)

            self._wake.clear()
            wake = asyncio.create_task(self._wake.wait())
            halt = asyncio.create_task(stop.wait())
            await asyncio.wait(
                {wake, halt}, timeout=self._poll_seconds, return_when=asyncio.FIRST_COMPLETED
            )
            for t in (wake, halt):
                t.cancel()

        await self._drain()
        logger.info("worker stopped", extra={"worker_id": self.worker_id})

    async def _drain(self) -> None:
        """On shutdown, give running jobs a chance to finish, then cancel them.

        A cancelled job is not lost: its lease lapses and the next worker to
        start recovers it.
        """
        if not self._active:
            return
        logger.info("worker draining", extra={"active_jobs": len(self._active)})
        _, pending = await asyncio.wait(self._active, timeout=10)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            logger.warning(
                "jobs cancelled at shutdown; they will be recovered when their lease lapses",
                extra={"count": len(pending)},
            )

    def health(self) -> dict[str, Any]:
        stale_after = max(self._poll_seconds * 3, 10)
        alive = (
            self.last_poll_at is not None
            and (datetime.now(UTC) - self.last_poll_at).total_seconds() <= stale_after
        )
        return {
            "healthy": alive,
            "detail": (
                f"polling (active={len(self._active)}, completed={self.jobs_completed}, "
                f"failed={self.jobs_failed}, retried={self.jobs_retried})"
                if alive
                else "not polling"
            ),
        }

    # ------------------------------------------------------------------
    # One job
    # ------------------------------------------------------------------

    async def _recover_stale(self) -> None:
        for recovered in await self.store.recover_stale_jobs():
            status = MeetingStatus.QUEUED if recovered.requeued else MeetingStatus.FAILED
            try:
                async with self._session_factory() as db:
                    await set_meeting_status(db, uuid.UUID(recovered.job.meeting_id), status)
            except Exception:
                logger.exception(
                    "could not update meeting status after recovery",
                    extra={"job_id": recovered.job.job_id},
                )

    async def _heartbeat(self, job: JobRecord) -> None:
        interval = max(self._lease_seconds / 3, 1)
        while True:
            await asyncio.sleep(interval)
            if not await self.store.renew_lease(
                job_id=job.job_id, worker_id=self.worker_id, lease_seconds=self._lease_seconds
            ):
                logger.warning(
                    "lease lost while running; outcome will be discarded",
                    extra={"job_id": job.job_id, "worker_id": self.worker_id},
                )
                return

    async def _process(self, job: JobRecord) -> None:
        meeting_id = uuid.UUID(job.meeting_id)
        heartbeat = asyncio.create_task(self._heartbeat(job), name=f"heartbeat-{job.job_id}")
        log_ctx = {"job_id": job.job_id, "meeting_id": job.meeting_id, "attempt": job.attempts}
        try:
            async with self._session_factory() as db:
                llm = self._llm_factory()
                meeting = await db.scalar(select(Meeting).where(Meeting.id == meeting_id))

                # The job carries the owner captured when the API authorised the
                # submission. A mismatch (or a deleted meeting) means the job no
                # longer describes anything this worker may act on.
                if meeting is None or str(meeting.owner_id) != job.owner_id:
                    if await self.store.fail(
                        job=job,
                        worker_id=self.worker_id,
                        error_code="meeting_not_found",
                        error_message="The meeting no longer exists.",
                    ):
                        self.jobs_failed += 1
                    return

                transcribed = False
                try:
                    media = await media_needing_transcription(db, meeting_id)

                    if (
                        media is None
                        and not job.force
                        and await is_result_current(db, meeting, model=llm.model)
                    ):
                        # Extraction is current, but the index may not be (a
                        # meeting processed before M6, or a new embedding model).
                        # Indexing is a no-op when it is current too.
                        index = await index_meeting(
                            db, meeting_id=meeting_id, embedder=self._embedder_factory()
                        )
                        minutes_index = await index_minutes(
                            db, meeting_id=meeting_id, embedder=self._embedder_factory()
                        )
                        outcome = await result_counts(db, meeting_id)
                        result = {
                            "minutes_chunks": minutes_index.chunks,
                            **outcome.as_job_result(),
                            "chunks": index.chunks,
                            "mom_pdf": await self._generate_minutes_pdf(db, job, meeting_id),
                        }
                        if await self.store.complete(
                            job=job, worker_id=self.worker_id, result=result
                        ):
                            self.jobs_completed += 1
                            # Recovery set the meeting back to queued; the stored
                            # results are complete, so restore the true state.
                            await set_meeting_status(db, meeting_id, MeetingStatus.COMPLETED)
                        return

                    # Stage 1 (recordings only): speech to text. Skipped when the
                    # transcript already came from this exact object, so a retry
                    # after an extraction failure does not transcribe twice.
                    if media is not None:
                        await self.store.add_event(
                            job=job, worker_id=self.worker_id, event_type="transcription_started"
                        )
                        transcript = await transcribe_meeting(
                            db,
                            meeting_id=meeting_id,
                            media=media,
                            storage=self.storage,
                            transcriber=self._transcriber_factory(),
                        )
                        transcribed = True
                        await self.store.add_event(
                            job=job,
                            worker_id=self.worker_id,
                            event_type="transcription_completed",
                            words=transcript.word_count,
                        )

                    # Stage 2 (M6): chunk and embed locally. Before extraction,
                    # so the meeting is searchable even if the LLM is unavailable.
                    index = await index_meeting(
                        db, meeting_id=meeting_id, embedder=self._embedder_factory()
                    )
                    if not index.cached:
                        await self.store.add_event(
                            job=job,
                            worker_id=self.worker_id,
                            event_type="indexing_completed",
                            chunks=index.chunks,
                        )

                    # Stage 3: the M2 extraction pipeline, unchanged.
                    outcome = await run_extraction(db, meeting_id=meeting_id, llm=llm)

                    # Stage 4 (M8): embed the minutes for Ask-your-meetings.
                    # Inside the try: an embedding failure is retried, and the
                    # retry finds the extraction current and only indexes.
                    minutes_index = await index_minutes(
                        db, meeting_id=meeting_id, embedder=self._embedder_factory()
                    )
                except Exception as exc:
                    await self._handle_failure(db, job, meeting_id, exc, log_ctx)
                    return

                # Stage 4 (M7): the Minutes of Meeting PDF, the product's output.
                result = {
                    **outcome.as_job_result(),
                    "transcribed": transcribed,
                    "chunks": index.chunks,
                    "minutes_chunks": minutes_index.chunks,
                    "mom_pdf": await self._generate_minutes_pdf(db, job, meeting_id),
                }
                if await self.store.complete(job=job, worker_id=self.worker_id, result=result):
                    self.jobs_completed += 1
                else:
                    # Results are already committed and correct; only this
                    # worker's claim to report them was superseded.
                    logger.warning("completed after losing lease", extra=log_ctx)
        except Exception:
            # Failures outside the extraction itself (e.g. DynamoDB unreachable
            # while recording the outcome). The lease will lapse and the job
            # will be recovered, so nothing is lost.
            logger.exception("job processing aborted", extra=log_ctx)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _generate_minutes_pdf(
        self, db: AsyncSession, job: JobRecord, meeting_id: uuid.UUID
    ) -> bool:
        """Render and store the MOM PDF. Never fails the job.

        By this point the minutes themselves are committed and complete. A PDF
        that cannot be stored now (e.g. storage briefly down) is rendered on
        demand when the user asks for it, so failing or retrying the whole job,
        and calling the LLM again, would gain nothing.
        """
        try:
            document = await ensure_minutes_pdf(db, meeting_id=meeting_id, storage=self.storage)
        except Exception:
            logger.exception("minutes pdf generation failed", extra={"job_id": job.job_id})
            await db.rollback()
            await self.store.add_event(
                job=job, worker_id=self.worker_id, event_type="mom_pdf_failed"
            )
            return False
        if not document.reused:
            await self.store.add_event(
                job=job,
                worker_id=self.worker_id,
                event_type="mom_pdf_generated",
                pages=document.pages,
                bytes=document.size_bytes,
            )
        return True

    async def _handle_failure(
        self,
        db: AsyncSession,
        job: JobRecord,
        meeting_id: uuid.UUID,
        exc: Exception,
        log_ctx: dict[str, Any],
    ) -> None:
        code, message, retryable = classify_error(exc)
        will_retry = retryable and job.attempts < job.max_attempts
        log = logger.warning if isinstance(exc, AppError) else logger.exception
        log(
            "job attempt failed",
            extra={**log_ctx, "error_code": code, "retryable": retryable, "will_retry": will_retry},
        )

        # DynamoDB transition first, conditional on still owning the job. Only
        # if that succeeds is the meeting status changed, so a worker whose
        # lease was taken over can never overwrite the new attempt's state.
        if will_retry:
            delay = self._retry_base_seconds * 4 ** (job.attempts - 1)
            if await self.store.requeue(
                job=job,
                worker_id=self.worker_id,
                delay_seconds=delay,
                error_code=code,
                error_message=message,
            ):
                self.jobs_retried += 1
                await set_meeting_status(db, meeting_id, MeetingStatus.QUEUED)
        elif await self.store.fail(
            job=job, worker_id=self.worker_id, error_code=code, error_message=message
        ):
            self.jobs_failed += 1
            await set_meeting_status(db, meeting_id, MeetingStatus.FAILED)


def build_worker() -> ProcessingWorker:
    """Worker wired to the application's real dependencies."""
    from app.db.session import AsyncSessionLocal
    from app.services.embeddings import get_embedder
    from app.services.job_store import get_job_store
    from app.services.llm.factory import get_llm_provider, get_transcription_provider
    from app.services.storage import get_storage

    return ProcessingWorker(
        store=get_job_store(),
        session_factory=AsyncSessionLocal,
        llm_factory=get_llm_provider,
        storage=get_storage(),
        transcriber_factory=get_transcription_provider,
        embedder_factory=get_embedder,
        concurrency=settings.worker_concurrency,
        poll_seconds=settings.worker_poll_seconds,
        lease_seconds=settings.job_lease_seconds,
        retry_base_seconds=settings.job_retry_base_seconds,
    )


async def _main() -> None:
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    worker = build_worker()
    if settings.dynamodb_auto_create_tables:
        await worker.store.ensure_table()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows' event loop does not support signal handlers; Ctrl+C there
        # raises KeyboardInterrupt instead, which stops the process directly.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    from app.db.session import engine

    try:
        await worker.run_forever(stop)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
