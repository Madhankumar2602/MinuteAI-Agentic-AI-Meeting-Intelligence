"""Recording upload, validation, transcription, and cleanup - end to end.

Uploads go to the real local S3-compatible container through real presigned
POST policies, exactly as a browser would send them. Only the transcription
model and the LLM are test doubles.
"""

from datetime import UTC, datetime, timedelta

import httpx
import jwt
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.db.models import MeetingMedia
from app.services.llm.base import LLMUnavailableError
from tests.fakes import PLATFORM_SYNC, TINY_WAV

TINY_WAV_SIZE = len(TINY_WAV)


async def _meeting(make_user, auth_headers, create_meeting, user=None):
    user = user or await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(headers, meeting_date="2026-09-10T10:00:00+00:00")
    return user, headers, meeting


async def _upload_url(
    client,
    meeting_id,
    headers,
    content_type="audio/wav",
    size=TINY_WAV_SIZE,
    filename="standup.wav",
):
    return await client.post(
        f"/api/v1/meetings/{meeting_id}/media/upload-url",
        json={"filename": filename, "content_type": content_type, "size_bytes": size},
        headers=headers,
    )


async def _storage_request(method: str, url: str, **kwargs) -> httpx.Response:
    # A real HTTP client, like a browser: talks to the storage container
    # directly, not through the ASGI test app.
    async with httpx.AsyncClient(timeout=30) as http:
        return await http.request(method, url, **kwargs)


async def _post_to_storage(
    ticket: dict, content: bytes, content_type: str = "audio/wav"
) -> httpx.Response:
    """What the browser does: multipart POST of the policy fields, then the file."""
    return await _storage_request(
        "POST",
        ticket["upload_url"],
        data=ticket["fields"],
        files={"file": ("recording", content, content_type)},
    )


async def _complete(client, meeting_id, headers, token, replace=False):
    return await client.post(
        f"/api/v1/meetings/{meeting_id}/media/complete",
        json={"upload_token": token, "replace_manual_transcript": replace},
        headers=headers,
    )


async def _uploaded(client, make_user, auth_headers, create_meeting, content=TINY_WAV):
    user, headers, meeting = await _meeting(make_user, auth_headers, create_meeting)
    ticket = (await _upload_url(client, meeting["id"], headers)).json()
    uploaded = await _post_to_storage(ticket, content)
    assert uploaded.status_code in (200, 204)
    done = await _complete(client, meeting["id"], headers, ticket["upload_token"])
    return user, headers, meeting, ticket, done


# ---------------------------------------------------------------------------
# Upload URL
# ---------------------------------------------------------------------------


async def test_upload_url_is_scoped_to_user_and_meeting(
    client: AsyncClient, make_user, auth_headers, create_meeting
) -> None:
    user, headers, meeting = await _meeting(make_user, auth_headers, create_meeting)

    response = await _upload_url(client, meeting["id"], headers, filename="../../evil.wav")
    assert response.status_code == 200
    body = response.json()

    key = body["fields"]["key"]
    assert key.startswith(f"users/{user.id}/meetings/{meeting['id']}/source/")
    assert key.endswith(".wav")
    assert "evil" not in key  # the client's filename never reaches the key
    assert body["fields"]["Content-Type"] == "audio/wav"
    assert body["max_bytes"] == settings.media_max_bytes


async def test_upload_url_rejects_unsupported_type_and_oversize(
    client: AsyncClient, make_user, auth_headers, create_meeting
) -> None:
    _, headers, meeting = await _meeting(make_user, auth_headers, create_meeting)

    html = await _upload_url(client, meeting["id"], headers, content_type="text/html")
    huge = await _upload_url(client, meeting["id"], headers, size=settings.media_max_bytes + 1)

    assert html.status_code == 422
    assert html.json()["error"]["code"] == "media_type_unsupported"
    assert huge.status_code == 422
    assert huge.json()["error"]["code"] == "media_too_large"


async def test_storage_itself_rejects_uploads_that_break_the_policy(
    client: AsyncClient, make_user, auth_headers, create_meeting, monkeypatch
) -> None:
    """Enforcement does not depend on the client behaving."""
    monkeypatch.setattr(settings, "media_max_bytes", 2048)
    _, headers, meeting = await _meeting(make_user, auth_headers, create_meeting)
    ticket = (await _upload_url(client, meeting["id"], headers, size=100)).json()

    too_big = await _post_to_storage(ticket, TINY_WAV + b"\x00" * 4096)
    wrong_type = await _storage_request(
        "POST",
        ticket["upload_url"],
        data={**ticket["fields"], "Content-Type": "text/html"},
        files={"file": ("x", b"<html>", "text/html")},
    )
    other_key = await _storage_request(
        "POST",
        ticket["upload_url"],
        data={**ticket["fields"], "key": "users/someone-else/meetings/x/source/pwned.wav"},
        files={"file": ("x", TINY_WAV, "audio/wav")},
    )

    assert too_big.status_code in (400, 403)
    assert wrong_type.status_code in (400, 403)
    assert other_key.status_code in (400, 403)


# ---------------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------------


async def test_complete_records_media_and_queues_processing(
    client: AsyncClient, make_user, auth_headers, create_meeting, job_store
) -> None:
    _, headers, meeting, ticket, done = await _uploaded(
        client, make_user, auth_headers, create_meeting
    )

    assert done.status_code == 202, done.text
    body = done.json()
    assert body["media"]["size_bytes"] == len(TINY_WAV)  # read from storage, not the client
    assert body["media"]["content_type"] == "audio/wav"
    assert body["media"]["original_filename"] == "standup.wav"
    assert body["job"]["status"] == "QUEUED"

    detail = (await client.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
    assert detail["status"] == "queued"
    assert detail["source_type"] == "audio"

    media = (await client.get(f"/api/v1/meetings/{meeting['id']}/media", headers=headers)).json()
    assert (await _storage_request("GET", media["download_url"])).content == TINY_WAV


async def test_complete_before_upload_is_409(
    client: AsyncClient, make_user, auth_headers, create_meeting
) -> None:
    _, headers, meeting = await _meeting(make_user, auth_headers, create_meeting)
    ticket = (await _upload_url(client, meeting["id"], headers)).json()

    response = await _complete(client, meeting["id"], headers, ticket["upload_token"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "upload_not_found"


async def test_disguised_file_is_rejected_and_deleted_from_storage(
    client: AsyncClient, make_user, auth_headers, create_meeting, storage, db_session
) -> None:
    _, headers, meeting = await _meeting(make_user, auth_headers, create_meeting)
    ticket = (await _upload_url(client, meeting["id"], headers)).json()
    # Passes the policy (declared audio/wav, small) but is not a WAV file.
    disguised = await _post_to_storage(
        ticket, b"<!doctype html><script>steal()</script>" + b" " * 64
    )
    assert disguised.status_code in (200, 204)

    response = await _complete(client, meeting["id"], headers, ticket["upload_token"])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "media_invalid"
    assert await storage.head(ticket["fields"]["key"]) is None
    assert (
        await db_session.scalar(
            select(MeetingMedia).where(MeetingMedia.meeting_id == meeting["id"])
        )
        is None
    )


async def test_upload_token_cannot_be_used_by_another_user_or_meeting(
    client: AsyncClient, make_user, auth_headers, create_meeting
) -> None:
    _, headers, meeting = await _meeting(make_user, auth_headers, create_meeting)
    ticket = (await _upload_url(client, meeting["id"], headers)).json()
    await _post_to_storage(ticket, TINY_WAV)

    _, intruder_headers, intruder_meeting = await _meeting(make_user, auth_headers, create_meeting)
    other_user = await _complete(client, meeting["id"], intruder_headers, ticket["upload_token"])
    own_meeting = await _complete(
        client, intruder_meeting["id"], intruder_headers, ticket["upload_token"]
    )
    other_meeting_same_user = await _complete(
        client, intruder_meeting["id"], headers, ticket["upload_token"]
    )

    assert other_user.status_code == 404
    assert own_meeting.status_code == 404
    assert other_meeting_same_user.status_code == 404


async def test_tampered_expired_and_access_tokens_are_rejected(
    client: AsyncClient, make_user, auth_headers, create_meeting
) -> None:
    user, headers, meeting = await _meeting(make_user, auth_headers, create_meeting)
    ticket = (await _upload_url(client, meeting["id"], headers)).json()

    tampered = await _complete(client, meeting["id"], headers, ticket["upload_token"][:-4] + "abcd")

    claims = jwt.decode(ticket["upload_token"], options={"verify_signature": False})
    claims["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    expired_token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    expired = await _complete(client, meeting["id"], headers, expired_token)

    access_token = headers["Authorization"].removeprefix("Bearer ")
    wrong_type = await _complete(client, meeting["id"], headers, access_token)

    assert tampered.status_code == 404
    assert expired.status_code == 404
    assert expired.json()["error"]["code"] == "upload_expired"
    assert wrong_type.status_code == 404


async def test_typed_transcript_takes_precedence_unless_replaced(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    _, headers, meeting = await _meeting(make_user, auth_headers, create_meeting)
    await put_transcript(meeting["id"], headers, PLATFORM_SYNC)
    ticket = (await _upload_url(client, meeting["id"], headers)).json()
    await _post_to_storage(ticket, TINY_WAV)

    refused = await _complete(client, meeting["id"], headers, ticket["upload_token"])
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "manual_transcript_exists"

    replaced = await _complete(client, meeting["id"], headers, ticket["upload_token"], replace=True)
    assert replaced.status_code == 202
    gone = await client.get(f"/api/v1/meetings/{meeting['id']}/transcript", headers=headers)
    assert gone.status_code == 404


# ---------------------------------------------------------------------------
# Processing a recording
# ---------------------------------------------------------------------------


async def test_worker_transcribes_then_extracts(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    worker,
    storage,
    fake_transcriber,
    fake_llm,
) -> None:
    _, headers, meeting, _, done = await _uploaded(client, make_user, auth_headers, create_meeting)
    job_id = done.json()["job"]["job_id"]

    await worker.run_once()

    # The transcriber received the real object, downloaded from storage.
    assert fake_transcriber.calls == [
        {"mime_type": "audio/wav", "size": len(TINY_WAV), "suffix": ".wav"}
    ]
    assert len(fake_llm.calls) == 1

    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "COMPLETED"
    assert job["result"]["transcribed"] is True
    assert job["result"]["action_items"] == 3
    assert [e["type"] for e in job["events"]] == [
        "queued",
        "started",
        "transcription_started",
        "transcription_completed",
        "indexing_completed",
        "mom_pdf_generated",
        "completed",
    ]

    transcript = (
        await client.get(f"/api/v1/meetings/{meeting['id']}/transcript", headers=headers)
    ).json()
    assert transcript["source"] == "transcription"
    assert transcript["content"].splitlines()[0] == PLATFORM_SYNC.splitlines()[0]
    assert transcript["transcription_model"] == "fake-transcribe-1"
    assert transcript["duration_seconds"] == 0  # TINY_WAV holds 0.1 s of audio
    assert transcript["media_id"] == done.json()["media"]["id"]

    # Raw structured output archived next to the recording.
    raw_key = f"users/{meeting['owner_id']}/meetings/{meeting['id']}/transcript/{done.json()['media']['id']}.json"
    assert await storage.head(raw_key) is not None


async def test_reprocessing_does_not_transcribe_the_same_recording_twice(
    client: AsyncClient, make_user, auth_headers, create_meeting, worker, fake_transcriber, fake_llm
) -> None:
    _, headers, meeting, _, _ = await _uploaded(client, make_user, auth_headers, create_meeting)
    await worker.run_once()

    forced = await client.post(
        f"/api/v1/meetings/{meeting['id']}/process?force=true", headers=headers
    )
    assert forced.status_code == 202
    await worker.run_once()

    assert len(fake_transcriber.calls) == 1  # transcript is current for this object
    assert len(fake_llm.calls) == 2  # extraction re-ran as requested


async def test_replacing_the_recording_triggers_fresh_transcription(
    client: AsyncClient, make_user, auth_headers, create_meeting, worker, storage, fake_transcriber
) -> None:
    _, headers, meeting, first_ticket, _ = await _uploaded(
        client, make_user, auth_headers, create_meeting
    )
    await worker.run_once()

    ticket = (await _upload_url(client, meeting["id"], headers, filename="take-two.wav")).json()
    await _post_to_storage(ticket, TINY_WAV + b"\x00" * 16)
    replaced = await _complete(client, meeting["id"], headers, ticket["upload_token"])
    assert replaced.status_code == 202
    await worker.run_once()

    assert len(fake_transcriber.calls) == 2
    assert await storage.head(first_ticket["fields"]["key"]) is None  # old recording removed
    media = (await client.get(f"/api/v1/meetings/{meeting['id']}/media", headers=headers)).json()
    assert media["original_filename"] == "take-two.wav"


async def test_transient_transcription_failure_is_retried(
    client: AsyncClient, make_user, auth_headers, create_meeting, worker, fake_transcriber, clock
) -> None:
    _, headers, meeting, _, done = await _uploaded(client, make_user, auth_headers, create_meeting)
    job_id = done.json()["job"]["job_id"]

    fake_transcriber.error = LLMUnavailableError()
    await worker.run_once()
    assert (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()[
        "status"
    ] == "QUEUED"

    fake_transcriber.error = None
    clock.advance(31)
    await worker.run_once()
    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "COMPLETED"
    assert job["attempts"] == 2


async def test_recording_without_speech_fails_with_clear_error(
    client: AsyncClient, make_user, auth_headers, create_meeting, worker, fake_transcriber, fake_llm
) -> None:
    from app.schemas.transcription import TranscriptionResult

    _, headers, meeting, _, done = await _uploaded(client, make_user, auth_headers, create_meeting)
    fake_transcriber.result = TranscriptionResult(language=None, segments=[])

    await worker.run_once()

    job = (await client.get(f"/api/v1/jobs/{done.json()['job']['job_id']}", headers=headers)).json()
    assert job["status"] == "FAILED"
    assert job["error"]["code"] == "no_speech_detected"
    assert fake_llm.calls == []  # extraction never ran on an empty transcript


async def test_process_endpoint_accepts_a_recording_without_a_transcript(
    client: AsyncClient, make_user, auth_headers, create_meeting
) -> None:
    """A recording is valid input on its own: not 409 transcript_missing."""
    _, headers, meeting, _, done = await _uploaded(client, make_user, auth_headers, create_meeting)
    assert (
        await client.get(f"/api/v1/meetings/{meeting['id']}/transcript", headers=headers)
    ).status_code == 404

    response = await client.post(f"/api/v1/meetings/{meeting['id']}/process", headers=headers)

    assert response.status_code == 202
    assert (
        response.json()["job"]["job_id"] == done.json()["job"]["job_id"]
    )  # the already-queued job


async def test_manual_transcript_clears_recording_provenance(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, worker
) -> None:
    _, headers, meeting, _, _ = await _uploaded(client, make_user, auth_headers, create_meeting)
    await worker.run_once()

    typed = await put_transcript(
        meeting["id"], headers, "Priya: A corrected, typed transcript of the meeting."
    )

    assert typed["source"] == "manual"
    assert typed["media_id"] is None
    assert typed["transcription_model"] is None


# ---------------------------------------------------------------------------
# Deletion and authorization
# ---------------------------------------------------------------------------


async def test_deleting_the_recording_keeps_its_transcript(
    client: AsyncClient, make_user, auth_headers, create_meeting, worker, storage
) -> None:
    _, headers, meeting, ticket, _ = await _uploaded(
        client, make_user, auth_headers, create_meeting
    )
    await worker.run_once()

    assert (
        await client.delete(f"/api/v1/meetings/{meeting['id']}/media", headers=headers)
    ).status_code == 204
    assert await storage.head(ticket["fields"]["key"]) is None
    assert (
        await client.get(f"/api/v1/meetings/{meeting['id']}/media", headers=headers)
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/meetings/{meeting['id']}/transcript", headers=headers)
    ).status_code == 200


async def test_deleting_a_meeting_removes_all_its_objects(
    client: AsyncClient, make_user, auth_headers, create_meeting, worker, storage
) -> None:
    _, headers, meeting, _, _ = await _uploaded(client, make_user, auth_headers, create_meeting)
    await worker.run_once()
    prefix = f"users/{meeting['owner_id']}/meetings/{meeting['id']}/"
    before = await storage._call("list_objects_v2", Bucket=storage.bucket, Prefix=prefix)
    assert before["KeyCount"] == 3  # the recording, its raw transcript, and the MOM PDF

    assert (
        await client.delete(f"/api/v1/meetings/{meeting['id']}", headers=headers)
    ).status_code == 204

    listing = await storage._call("list_objects_v2", Bucket=storage.bucket, Prefix=prefix)
    assert listing.get("KeyCount", 0) == 0


async def test_other_user_cannot_touch_the_recording(
    client: AsyncClient, make_user, auth_headers, create_meeting
) -> None:
    _, _, meeting, _, _ = await _uploaded(client, make_user, auth_headers, create_meeting)
    intruder = await make_user()
    intruder_headers = await auth_headers(intruder)
    base = f"/api/v1/meetings/{meeting['id']}/media"

    attempts = [
        await client.get(base, headers=intruder_headers),
        await client.delete(base, headers=intruder_headers),
        await _upload_url(client, meeting["id"], intruder_headers),
    ]
    assert [r.status_code for r in attempts] == [404, 404, 404]


# ---------------------------------------------------------------------------
# Video (M7): the audio track is extracted before transcription
# ---------------------------------------------------------------------------


async def _uploaded_video(client, make_user, auth_headers, create_meeting, content: bytes):
    user, headers, meeting = await _meeting(make_user, auth_headers, create_meeting)
    ticket = (
        await _upload_url(
            client,
            meeting["id"],
            headers,
            content_type="video/mp4",
            size=len(content),
            filename="all-hands.mp4",
        )
    ).json()
    uploaded = await _post_to_storage(ticket, content, content_type="video/mp4")
    assert uploaded.status_code in (200, 204), uploaded.text
    done = await _complete(client, meeting["id"], headers, ticket["upload_token"])
    assert done.status_code == 202, done.text
    return headers, meeting, done.json()["job"]["job_id"]


async def test_video_is_transcribed_from_its_extracted_audio_track(
    client: AsyncClient, make_user, auth_headers, create_meeting, worker, fake_transcriber
) -> None:
    from tests.media_factory import make_video

    video = make_video(seconds=5)
    headers, meeting, job_id = await _uploaded_video(
        client, make_user, auth_headers, create_meeting, video
    )

    await worker.run_once()

    # Gemini receives a small Opus file, never the video itself.
    [call] = fake_transcriber.calls
    assert call["mime_type"] == "audio/ogg"
    assert call["suffix"] == ".ogg"
    assert call["size"] < len(video)

    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "COMPLETED", job
    meeting_now = (await client.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
    assert meeting_now["source_type"] == "video"
    transcript = (
        await client.get(f"/api/v1/meetings/{meeting['id']}/transcript", headers=headers)
    ).json()
    assert transcript["duration_seconds"] == 5


async def test_video_without_audio_fails_with_a_clear_reason_and_no_llm_call(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    worker,
    fake_transcriber,
    fake_llm,
) -> None:
    from tests.media_factory import make_video

    headers, meeting, job_id = await _uploaded_video(
        client, make_user, auth_headers, create_meeting, make_video(seconds=2, audio=False)
    )

    await worker.run_once()

    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "FAILED"  # permanent: retrying cannot add a soundtrack
    assert job["error"]["code"] == "no_audio_track"
    assert fake_transcriber.calls == [] and fake_llm.calls == []


async def test_typed_notes_also_take_precedence_over_a_recording(
    client: AsyncClient, make_user, auth_headers, create_meeting
) -> None:
    _, headers, meeting = await _meeting(make_user, auth_headers, create_meeting)
    saved = await client.put(
        f"/api/v1/meetings/{meeting['id']}/transcript",
        json={"content": "Budget approved. Leela sends the forecast by Friday.", "kind": "notes"},
        headers=headers,
    )
    assert saved.json()["source"] == "notes"
    ticket = (await _upload_url(client, meeting["id"], headers)).json()
    await _post_to_storage(ticket, TINY_WAV)

    refused = await _complete(client, meeting["id"], headers, ticket["upload_token"])
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "manual_transcript_exists"
