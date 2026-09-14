"""Asynchronous processing through the API: submission, worker, retries, recovery."""

from httpx import AsyncClient
from sqlalchemy import text

from app.services.llm.base import (
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMResponseError,
    LLMUnavailableError,
)
from app.workers.processing import classify_error
from tests.fakes import PLATFORM_SYNC


async def _ready_meeting(make_user, auth_headers, create_meeting, put_transcript):
    user = await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(headers, meeting_date="2026-09-10T10:00:00+00:00")
    await put_transcript(meeting["id"], headers, PLATFORM_SYNC)
    return headers, meeting


async def _submit(client: AsyncClient, meeting_id: str, headers: dict, force: bool = False):
    url = f"/api/v1/meetings/{meeting_id}/process" + ("?force=true" if force else "")
    return await client.post(url, headers=headers)


async def _meeting_status(client: AsyncClient, meeting_id: str, headers: dict) -> str:
    return (await client.get(f"/api/v1/meetings/{meeting_id}", headers=headers)).json()["status"]


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


async def test_submit_returns_202_with_queued_job_immediately(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, fake_llm
) -> None:
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)

    response = await _submit(client, meeting["id"], headers)

    assert response.status_code == 202
    body = response.json()
    assert body["cached"] is False
    assert body["meeting_status"] == "queued"
    job = body["job"]
    assert job["status"] == "QUEUED"
    assert job["attempts"] == 0
    assert job["max_attempts"] == 3
    assert [e["type"] for e in job["events"]] == ["queued"]
    # Nothing ran yet: the API did not wait for the LLM.
    assert fake_llm.calls == []
    assert await _meeting_status(client, meeting["id"], headers) == "queued"


async def test_duplicate_submission_returns_the_same_active_job(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, job_store
) -> None:
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)

    first = (await _submit(client, meeting["id"], headers)).json()["job"]
    second = await _submit(client, meeting["id"], headers)

    assert second.status_code == 202
    assert second.json()["job"]["job_id"] == first["job_id"]
    assert len(await job_store.list_jobs_for_meeting(meeting["id"])) == 1


async def test_transcript_cannot_change_while_queued(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    await _submit(client, meeting["id"], headers)

    response = await client.put(
        f"/api/v1/meetings/{meeting['id']}/transcript",
        json={"content": "A different transcript that should be refused."},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "processing_in_progress"


# ---------------------------------------------------------------------------
# Worker: success
# ---------------------------------------------------------------------------


async def test_worker_completes_job_and_records_result(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, worker
) -> None:
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    job_id = (await _submit(client, meeting["id"], headers)).json()["job"]["job_id"]

    assert await worker.run_once() == 1

    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "COMPLETED"
    assert job["attempts"] == 1
    assert job["error"] is None
    assert job["started_at"] and job["finished_at"]
    assert job["result"] == {
        "cached": False,
        "transcribed": False,  # text transcript: no recording to transcribe
        "decisions": 2,
        "action_items": 3,
        "participants": 4,
        "warnings": [],
        "chunks": 3,  # ~320 words at 160 (fake) tokens per chunk, with overlap
        "mom_pdf": True,  # the Minutes of Meeting PDF was stored (M7)
    }
    assert [e["type"] for e in job["events"]] == [
        "queued",
        "started",
        "indexing_completed",
        "mom_pdf_generated",
        "completed",
    ]
    assert job["events"][1]["detail"]["worker_id"] == "test-worker"
    assert job["events"][2]["detail"]["chunks"] == 3
    assert await _meeting_status(client, meeting["id"], headers) == "completed"


async def test_meeting_job_history_is_newest_first(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, worker, clock
) -> None:
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    first = (await _submit(client, meeting["id"], headers)).json()["job"]["job_id"]
    await worker.run_once()
    # The fake clock only moves when told to. Two jobs for one meeting can never
    # share a millisecond in reality (the meeting lock serialises them), but a
    # frozen clock would give them identical sort keys.
    clock.advance(1)
    second = (await _submit(client, meeting["id"], headers, force=True)).json()["job"]["job_id"]

    history = (await client.get(f"/api/v1/meetings/{meeting['id']}/jobs", headers=headers)).json()
    assert [j["job_id"] for j in history] == [second, first]
    assert [j["status"] for j in history] == ["QUEUED", "COMPLETED"]


# ---------------------------------------------------------------------------
# Worker: failures and retries
# ---------------------------------------------------------------------------


async def test_transient_llm_error_is_retried_with_backoff_then_succeeds(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    worker,
    fake_llm,
    fake_embedder,
    clock,
) -> None:
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    job_id = (await _submit(client, meeting["id"], headers)).json()["job"]["job_id"]

    fake_llm.error = LLMRateLimitError()
    await worker.run_once()

    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "QUEUED"  # scheduled for retry, not failed
    assert job["attempts"] == 1
    assert job["error"]["code"] == "llm_rate_limited"
    assert job["next_attempt_at"] > job["updated_at"]
    assert await _meeting_status(client, meeting["id"], headers) == "queued"

    # Before the back-off elapses nothing runs, even though the provider recovered.
    fake_llm.error = None
    assert await worker.run_once() == 0

    clock.advance(31)  # retry_base_seconds = 30
    assert await worker.run_once() == 1

    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "COMPLETED"
    assert job["attempts"] == 2
    assert job["error"] is None
    assert [e["type"] for e in job["events"]] == [
        "queued",
        "started",
        "indexing_completed",
        "retry_scheduled",
        "started",  # the index is current, so the retry goes straight to extraction
        "mom_pdf_generated",
        "completed",
    ]
    assert fake_embedder.document_calls == [3]  # one batch of 3 chunks across both attempts
    assert await _meeting_status(client, meeting["id"], headers) == "completed"


async def test_retries_stop_after_max_attempts(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    worker,
    fake_llm,
    clock,
) -> None:
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    job_id = (await _submit(client, meeting["id"], headers)).json()["job"]["job_id"]
    fake_llm.error = LLMUnavailableError()

    for _ in range(3):
        await worker.run_once()
        clock.advance(10_000)  # past any back-off

    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "FAILED"
    assert job["attempts"] == 3
    assert job["error"]["code"] == "llm_unavailable"
    assert len(fake_llm.calls) == 3
    assert await _meeting_status(client, meeting["id"], headers) == "failed"


async def test_permanent_error_fails_immediately_without_retry(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, worker, fake_llm
) -> None:
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    job_id = (await _submit(client, meeting["id"], headers)).json()["job"]["job_id"]
    fake_llm.error = LLMResponseError()

    await worker.run_once()

    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "FAILED"
    assert job["attempts"] == 1
    assert job["error"] == {
        "code": "llm_invalid_response",
        "message": "The AI provider returned an unusable response.",
    }
    assert len(fake_llm.calls) == 1


async def test_failed_meeting_can_be_resubmitted(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, worker, fake_llm
) -> None:
    """Failures are recoverable: the lock is released, so a new job is accepted."""
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    failed_id = (await _submit(client, meeting["id"], headers)).json()["job"]["job_id"]
    fake_llm.error = LLMNotConfiguredError()
    await worker.run_once()

    fake_llm.error = None
    retry = await _submit(client, meeting["id"], headers)
    assert retry.status_code == 202
    assert retry.json()["job"]["job_id"] != failed_id
    await worker.run_once()
    assert await _meeting_status(client, meeting["id"], headers) == "completed"


async def test_job_for_deleted_meeting_fails_cleanly(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    worker,
    job_store,
    fake_llm,
) -> None:
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    job_id = (await _submit(client, meeting["id"], headers)).json()["job"]["job_id"]
    await client.delete(f"/api/v1/meetings/{meeting['id']}", headers=headers)

    await worker.run_once()

    job = await job_store.get_job(job_id)
    assert job.status.value == "FAILED"
    assert job.error_code == "meeting_not_found"
    assert fake_llm.calls == []
    # And the job is no longer visible through the API: its meeting is gone.
    assert (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).status_code == 404


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


async def test_job_abandoned_by_crashed_worker_is_recovered_and_completed(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    worker,
    job_store,
    db_session,
    clock,
) -> None:
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    job_id = (await _submit(client, meeting["id"], headers)).json()["job"]["job_id"]

    # Simulate a worker that claimed the job, set the meeting PROCESSING, then died.
    await job_store.claim_next(worker_id="crashed-worker", lease_seconds=60)
    await db_session.execute(
        text("UPDATE meetings SET status = 'processing' WHERE id = :id"), {"id": meeting["id"]}
    )
    await db_session.commit()

    assert await worker.run_once() == 0  # lease still valid: not touched
    clock.advance(61)
    assert await worker.run_once() == 1  # recovered, requeued, and run

    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "COMPLETED"
    assert job["attempts"] == 2
    assert "lease_expired_requeued" in [e["type"] for e in job["events"]]
    assert await _meeting_status(client, meeting["id"], headers) == "completed"


async def test_recovered_job_whose_results_were_already_saved_skips_the_llm(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    worker,
    job_store,
    fake_llm,
    clock,
) -> None:
    """Crash AFTER results were committed but BEFORE the job was marked complete."""
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    await process_and_wait(meeting["id"], headers)  # results now current
    assert len(fake_llm.calls) == 1

    # A non-force job whose worker "died" just before reporting completion.
    job, _ = await job_store.create_job(
        meeting_id=meeting["id"], owner_id=meeting["owner_id"], force=False
    )
    await job_store.claim_next(worker_id="crashed-worker", lease_seconds=60)
    clock.advance(61)

    await worker.run_once()

    recovered = await job_store.get_job(job.job_id)
    assert recovered.status.value == "COMPLETED"
    assert recovered.result["cached"] is True
    assert len(fake_llm.calls) == 1  # no second LLM call
    # Recovery had reset the meeting to queued; the cache hit restores it.
    assert await _meeting_status(client, meeting["id"], headers) == "completed"


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def test_other_user_cannot_see_a_job(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    job_id = (await _submit(client, meeting["id"], headers)).json()["job"]["job_id"]

    intruder = await make_user()
    intruder_headers = await auth_headers(intruder)

    response = await client.get(f"/api/v1/jobs/{job_id}", headers=intruder_headers)
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Job not found."


async def test_unknown_and_malformed_job_ids(client: AsyncClient, make_user, auth_headers) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    assert (
        await client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000", headers=headers)
    ).status_code == 404
    assert (await client.get("/api/v1/jobs/not-a-uuid", headers=headers)).status_code == 422


# ---------------------------------------------------------------------------
# Error classification (pure)
# ---------------------------------------------------------------------------


def test_error_classification() -> None:
    assert classify_error(LLMRateLimitError())[2] is True
    assert classify_error(LLMUnavailableError())[2] is True
    assert classify_error(LLMResponseError())[2] is False
    assert classify_error(LLMNotConfiguredError())[2] is False

    code, message, retryable = classify_error(RuntimeError("SELECT secret FROM internals"))
    assert (code, retryable) == ("internal_error", False)
    assert "secret" not in message  # raw exception text never reaches the client


# ---------------------------------------------------------------------------
# Long-running loop
# ---------------------------------------------------------------------------


async def test_run_forever_wakes_on_notify_processes_job_and_stops_cleanly(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, worker, job_store
) -> None:
    """Exercises the real loop (not run_once): notify wake-up, claim, completion, shutdown."""
    import asyncio

    headers, meeting = await _ready_meeting(make_user, auth_headers, create_meeting, put_transcript)
    job_id = (await _submit(client, meeting["id"], headers)).json()["job"]["job_id"]

    stop = asyncio.Event()
    loop_task = asyncio.create_task(worker.run_forever(stop))
    worker.notify()

    job = None
    for _ in range(100):
        job = await job_store.get_job(job_id)
        if job.is_terminal:
            break
        await asyncio.sleep(0.05)

    assert job.status.value == "COMPLETED"
    assert worker.health()["healthy"] is True
    assert worker.jobs_completed == 1

    stop.set()
    await asyncio.wait_for(loop_task, timeout=5)
    assert loop_task.done() and loop_task.exception() is None
