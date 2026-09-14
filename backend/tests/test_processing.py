"""Transcript input and the extraction pipeline, end to end through the API.

The LLM is the deterministic ``FakeLLMProvider`` (see tests/fakes.py); every
other layer - routes, authorization, job queue, worker, normalisation,
grounding, persistence, status transitions - is the real code against the real
test database and DynamoDB Local.

Job lifecycle specifics (retries, leases, duplicate submissions) live in
test_jobs.py; this file is about what processing produces.
"""

from httpx import AsyncClient

from app.services.llm.base import LLMResponseError
from tests.fakes import PLATFORM_SYNC


async def _meeting_with_transcript(make_user, auth_headers, create_meeting, put_transcript):
    user = await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(
        headers, title="Platform sync", meeting_date="2026-09-10T10:00:00+00:00"
    )
    await put_transcript(meeting["id"], headers, PLATFORM_SYNC)
    return user, headers, meeting


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------


async def test_put_transcript_stores_content_and_metadata(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(headers)

    body = await put_transcript(
        meeting["id"], headers, "Alice: hello there.\r\nBob: hi, let's begin."
    )

    # Windows line endings normalised, so the hash is platform-independent.
    assert body["content"] == "Alice: hello there.\nBob: hi, let's begin."
    assert body["word_count"] == 7
    assert len(body["content_sha256"]) == 64
    assert body["source"] == "manual"

    fetched = await client.get(f"/api/v1/meetings/{meeting['id']}/transcript", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["content_sha256"] == body["content_sha256"]


async def test_put_transcript_replaces_existing_one(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(headers)

    first = await put_transcript(meeting["id"], headers, "Version one of the transcript text.")
    second = await put_transcript(meeting["id"], headers, "Version two of the transcript text.")

    assert first["id"] == second["id"]  # replaced in place, not duplicated
    assert first["content_sha256"] != second["content_sha256"]


async def test_transcript_rejects_too_short_and_nul_content(
    client: AsyncClient, make_user, auth_headers, create_meeting
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(headers)
    url = f"/api/v1/meetings/{meeting['id']}/transcript"

    too_short = await client.put(url, json={"content": "hi"}, headers=headers)
    whitespace = await client.put(url, json={"content": " " * 50}, headers=headers)
    nul = await client.put(
        url, json={"content": "valid looking text\x00with a NUL"}, headers=headers
    )

    assert too_short.status_code == 422
    assert whitespace.status_code == 422
    assert nul.status_code == 422  # would otherwise be a 500 from PostgreSQL


async def test_get_transcript_when_none_exists_is_404(
    client: AsyncClient, make_user, auth_headers, create_meeting
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(headers)
    response = await client.get(f"/api/v1/meetings/{meeting['id']}/transcript", headers=headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Processing: results
# ---------------------------------------------------------------------------


async def test_processing_extracts_and_persists_everything(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    _, headers, meeting = await _meeting_with_transcript(
        make_user, auth_headers, create_meeting, put_transcript
    )

    body = await process_and_wait(meeting["id"], headers)

    assert body["submission"]["cached"] is False
    assert body["status"] == "completed"
    assert len(fake_llm.calls) == 1

    # The prompt carried the meeting date and fenced the transcript as the input.
    prompt = fake_llm.calls[0]["prompt"]
    assert "MEETING DATE: 2026-09-10 (Thursday)" in prompt
    assert "INPUT TYPE: TRANSCRIPT" in prompt
    assert "<<<INPUT START>>>" in prompt

    summary = body["summary"]
    assert summary["prompt_version"] == "extract-v2"
    assert summary["model"] == "fake-model-1"
    assert summary["is_stale"] is False

    # Alphabetical: every participant row is inserted in one transaction, so
    # created_at is identical and cannot express extraction order.
    assert [p["display_name"] for p in body["participants"]] == [
        "Arjun",
        "Karthik",
        "Meera",
        "Priya",
    ]
    assert len(body["decisions"]) == 2
    assert all(d["evidence_verified"] for d in body["decisions"])

    items = body["action_items"]
    assert [i["owner_name"] for i in items] == ["Karthik", "Meera", "Arjun"]
    participant_ids = {p["id"] for p in body["participants"]}
    assert all(i["owner_participant_id"] in participant_ids for i in items)
    assert items[0]["deadline"] == "2026-09-16"
    assert items[0]["deadline_text"] == "by next Wednesday"
    assert items[1]["priority"] == "high"
    assert all(i["status"] == "pending" for i in items)


async def test_results_are_readable_through_individual_endpoints(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
) -> None:
    _, headers, meeting = await _meeting_with_transcript(
        make_user, auth_headers, create_meeting, put_transcript
    )
    await process_and_wait(meeting["id"], headers)
    base = f"/api/v1/meetings/{meeting['id']}"

    assert (await client.get(f"{base}/summary", headers=headers)).status_code == 200
    assert len((await client.get(f"{base}/decisions", headers=headers)).json()) == 2
    assert len((await client.get(f"{base}/action-items", headers=headers)).json()) == 3
    assert len((await client.get(f"{base}/participants", headers=headers)).json()) == 4
    assert (await client.get(base, headers=headers)).json()["status"] == "completed"


async def test_unchanged_transcript_is_served_from_cache_without_a_job(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    _, headers, meeting = await _meeting_with_transcript(
        make_user, auth_headers, create_meeting, put_transcript
    )
    await process_and_wait(meeting["id"], headers)

    second = await client.post(f"/api/v1/meetings/{meeting['id']}/process", headers=headers)

    assert second.status_code == 200
    assert second.json() == {"cached": True, "meeting_status": "completed", "job": None}
    assert len(fake_llm.calls) == 1  # no second paid LLM call


async def test_force_reprocesses_and_resets_manual_changes(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    _, headers, meeting = await _meeting_with_transcript(
        make_user, auth_headers, create_meeting, put_transcript
    )
    first = await process_and_wait(meeting["id"], headers)
    item_id = first["action_items"][0]["id"]
    await client.patch(f"/api/v1/action-items/{item_id}", json={"status": "done"}, headers=headers)

    forced = await process_and_wait(meeting["id"], headers, force=True)

    assert forced["submission"]["job"]["force"] is True
    assert len(fake_llm.calls) == 2
    # Documented behaviour: results are re-derived, so no duplicates and the
    # manual status change is reset.
    assert len(forced["action_items"]) == 3
    assert all(i["status"] == "pending" for i in forced["action_items"])


async def test_changed_transcript_marks_summary_stale_then_reprocesses(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    _, headers, meeting = await _meeting_with_transcript(
        make_user, auth_headers, create_meeting, put_transcript
    )
    url = f"/api/v1/meetings/{meeting['id']}"
    await process_and_wait(meeting["id"], headers)

    await put_transcript(meeting["id"], headers, PLATFORM_SYNC + "\nPriya: One more thing.")
    assert (await client.get(f"{url}/summary", headers=headers)).json()["is_stale"] is True

    rerun = await process_and_wait(meeting["id"], headers)
    assert rerun["submission"]["cached"] is False  # transcript changed, so no cache hit
    assert rerun["summary"]["is_stale"] is False
    assert len(fake_llm.calls) == 2


# ---------------------------------------------------------------------------
# Processing: failure paths
# ---------------------------------------------------------------------------


async def test_process_without_transcript_is_409_and_queues_nothing(
    client: AsyncClient, make_user, auth_headers, create_meeting, job_store, fake_llm
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(headers)

    response = await client.post(f"/api/v1/meetings/{meeting['id']}/process", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "transcript_missing"
    assert await job_store.list_jobs_for_meeting(meeting["id"]) == []
    assert fake_llm.calls == []


async def test_failed_reprocess_keeps_previous_results_intact(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    worker,
    fake_llm,
) -> None:
    """A failed re-run must roll back, not leave a meeting with half its old items deleted."""
    _, headers, meeting = await _meeting_with_transcript(
        make_user, auth_headers, create_meeting, put_transcript
    )
    url = f"/api/v1/meetings/{meeting['id']}"
    await process_and_wait(meeting["id"], headers)

    fake_llm.error = LLMResponseError()
    await client.post(f"{url}/process?force=true", headers=headers)
    await worker.run_once()

    assert (await client.get(url, headers=headers)).json()["status"] == "failed"
    assert len((await client.get(f"{url}/action-items", headers=headers)).json()) == 3


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def test_other_user_cannot_touch_transcript_processing_or_results(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    job_store,
    fake_llm,
) -> None:
    _, _, meeting = await _meeting_with_transcript(
        make_user, auth_headers, create_meeting, put_transcript
    )
    intruder = await make_user()
    intruder_headers = await auth_headers(intruder)
    base = f"/api/v1/meetings/{meeting['id']}"

    attempts = [
        await client.get(f"{base}/transcript", headers=intruder_headers),
        await client.put(
            f"{base}/transcript",
            json={"content": "overwritten by someone else"},
            headers=intruder_headers,
        ),
        await client.post(f"{base}/process", headers=intruder_headers),
        await client.get(f"{base}/summary", headers=intruder_headers),
        await client.get(f"{base}/decisions", headers=intruder_headers),
        await client.get(f"{base}/action-items", headers=intruder_headers),
        await client.get(f"{base}/participants", headers=intruder_headers),
        await client.get(f"{base}/intelligence", headers=intruder_headers),
        await client.get(f"{base}/jobs", headers=intruder_headers),
    ]
    assert [r.status_code for r in attempts] == [404] * len(attempts)
    # The intruder's submission never created a job, let alone reached the LLM.
    assert await job_store.list_jobs_for_meeting(meeting["id"]) == []
    assert fake_llm.calls == []
