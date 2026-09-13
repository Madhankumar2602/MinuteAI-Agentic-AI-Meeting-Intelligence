"""JobStore against real DynamoDB Local: the guarantees the worker relies on.

No HTTP and no PostgreSQL. Time is controlled with the injected ``clock``
fixture, so lease expiry and back-off are tested without sleeping.
"""

import asyncio
import uuid

from app.services.job_store import JobStatus, JobStore


async def _new_job(store: JobStore, force: bool = False):
    job, created = await store.create_job(
        meeting_id=uuid.uuid4(), owner_id=uuid.uuid4(), force=force
    )
    assert created
    return job


async def test_create_job_is_queued_with_event_and_ttl(job_store: JobStore) -> None:
    job = await _new_job(job_store)
    stored = await job_store.get_job(job.job_id)

    assert stored.status is JobStatus.QUEUED
    assert stored.attempts == 0
    assert [e["type"] for e in stored.events] == ["queued"]

    raw = job_store._client.get_item(
        TableName=job_store.table_name, Key={"pk": {"S": f"JOB#{job.job_id}"}}
    )["Item"]
    assert int(raw["expires_at"]["N"]) > 0  # TTL attribute present


async def test_second_submission_for_same_meeting_returns_active_job(job_store: JobStore) -> None:
    meeting_id, owner_id = uuid.uuid4(), uuid.uuid4()
    first, created_first = await job_store.create_job(
        meeting_id=meeting_id, owner_id=owner_id, force=False
    )
    second, created_second = await job_store.create_job(
        meeting_id=meeting_id, owner_id=owner_id, force=True
    )

    assert created_first and not created_second
    assert second.job_id == first.job_id
    assert len(await job_store.list_jobs_for_meeting(meeting_id)) == 1


async def test_concurrent_submissions_create_exactly_one_job(job_store: JobStore) -> None:
    """The transaction's lock condition, not a read-then-write check, enforces this."""
    meeting_id, owner_id = uuid.uuid4(), uuid.uuid4()
    results = await asyncio.gather(
        *(
            job_store.create_job(meeting_id=meeting_id, owner_id=owner_id, force=False)
            for _ in range(5)
        )
    )
    assert sum(created for _, created in results) == 1
    assert len({job.job_id for job, _ in results}) == 1


async def test_concurrent_claims_have_exactly_one_winner(job_store: JobStore) -> None:
    await _new_job(job_store)
    claims = await asyncio.gather(
        *(job_store.claim_next(worker_id=f"w{i}", lease_seconds=60) for i in range(5))
    )
    winners = [c for c in claims if c is not None]
    assert len(winners) == 1
    assert winners[0].status is JobStatus.PROCESSING
    assert winners[0].attempts == 1


async def test_claims_are_fifo(job_store: JobStore, clock) -> None:
    older = await _new_job(job_store)
    clock.advance(1)
    await _new_job(job_store)

    claimed = await job_store.claim_next(worker_id="w", lease_seconds=60)
    assert claimed.job_id == older.job_id


async def test_complete_releases_the_lock_and_records_result(job_store: JobStore) -> None:
    meeting_id, owner_id = uuid.uuid4(), uuid.uuid4()
    job, _ = await job_store.create_job(meeting_id=meeting_id, owner_id=owner_id, force=False)
    claimed = await job_store.claim_next(worker_id="w", lease_seconds=60)

    assert await job_store.complete(
        job=claimed, worker_id="w", result={"cached": False, "decisions": 2}
    )

    done = await job_store.get_job(job.job_id)
    assert done.status is JobStatus.COMPLETED
    assert done.result == {"cached": False, "decisions": 2}
    assert done.finished_at is not None
    # Lock released: a new submission for the same meeting creates a new job.
    _, created = await job_store.create_job(meeting_id=meeting_id, owner_id=owner_id, force=False)
    assert created


async def test_requeued_job_waits_for_its_backoff(job_store: JobStore, clock) -> None:
    await _new_job(job_store)
    claimed = await job_store.claim_next(worker_id="w", lease_seconds=60)
    assert await job_store.requeue(
        job=claimed,
        worker_id="w",
        delay_seconds=300,
        error_code="llm_rate_limited",
        error_message="x",
    )

    assert await job_store.claim_next(worker_id="w", lease_seconds=60) is None
    clock.advance(301)
    again = await job_store.claim_next(worker_id="w", lease_seconds=60)
    assert again is not None
    assert again.attempts == 2
    assert again.error_code == "llm_rate_limited"  # last error kept for visibility


async def test_expired_lease_is_recovered_and_stale_worker_is_fenced_out(
    job_store: JobStore, clock
) -> None:
    job = await _new_job(job_store)
    crashed = await job_store.claim_next(worker_id="crashed", lease_seconds=60)

    clock.advance(61)
    recovered = await job_store.recover_stale_jobs()
    assert [(r.job.job_id, r.requeued) for r in recovered] == [(job.job_id, True)]

    # The "dead" worker wakes up and tries to report a result: rejected, so it
    # cannot overwrite whatever the next attempt produces.
    assert not await job_store.complete(job=crashed, worker_id="crashed", result={"cached": False})
    assert not await job_store.renew_lease(job_id=job.job_id, worker_id="crashed", lease_seconds=60)

    retry = await job_store.claim_next(worker_id="healthy", lease_seconds=60)
    assert retry.attempts == 2
    assert "lease_expired_requeued" in [e["type"] for e in retry.events]


async def test_renewed_lease_is_not_recovered(job_store: JobStore, clock) -> None:
    job = await _new_job(job_store)
    await job_store.claim_next(worker_id="w", lease_seconds=60)

    clock.advance(50)
    assert await job_store.renew_lease(job_id=job.job_id, worker_id="w", lease_seconds=60)
    clock.advance(50)  # 100s after claim, but only 50s after the heartbeat

    assert await job_store.recover_stale_jobs() == []


async def test_lease_expiry_on_last_attempt_fails_job_and_releases_lock(
    job_store: JobStore, clock
) -> None:
    meeting_id, owner_id = uuid.uuid4(), uuid.uuid4()
    job, _ = await job_store.create_job(meeting_id=meeting_id, owner_id=owner_id, force=False)

    for _ in range(3):  # max_attempts = 3
        assert await job_store.claim_next(worker_id="w", lease_seconds=60) is not None
        clock.advance(61)
        recovered = await job_store.recover_stale_jobs()

    assert recovered[0].requeued is False
    final = await job_store.get_job(job.job_id)
    assert final.status is JobStatus.FAILED
    assert final.error_code == "lease_expired"
    _, created = await job_store.create_job(meeting_id=meeting_id, owner_id=owner_id, force=False)
    assert created


async def test_history_is_newest_first(job_store: JobStore, clock) -> None:
    meeting_id, owner_id = uuid.uuid4(), uuid.uuid4()
    first, _ = await job_store.create_job(meeting_id=meeting_id, owner_id=owner_id, force=False)
    claimed = await job_store.claim_next(worker_id="w", lease_seconds=60)
    await job_store.fail(job=claimed, worker_id="w", error_code="x", error_message="x")
    clock.advance(1)
    second, _ = await job_store.create_job(meeting_id=meeting_id, owner_id=owner_id, force=False)

    history = await job_store.list_jobs_for_meeting(meeting_id)
    assert [j.job_id for j in history] == [second.job_id, first.job_id]


async def test_unknown_job_is_none(job_store: JobStore) -> None:
    assert await job_store.get_job(str(uuid.uuid4())) is None
