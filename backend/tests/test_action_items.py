"""Cross-meeting action items, human corrections, and decision updates."""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.schemas.extraction import ExtractedActionItem, MeetingExtraction
from tests.fakes import PLATFORM_SYNC, platform_sync_extraction


async def _processed_meeting(
    client, make_user, auth_headers, create_meeting, put_transcript, user=None
):
    user = user or await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(
        headers, title="Platform sync", meeting_date="2026-09-10T10:00:00+00:00"
    )
    await put_transcript(meeting["id"], headers, PLATFORM_SYNC)
    body = (await client.post(f"/api/v1/meetings/{meeting['id']}/process", headers=headers)).json()
    return user, headers, meeting, body


async def test_list_action_items_returns_only_my_items_across_meetings(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    user, headers, _, _ = await _processed_meeting(
        client, make_user, auth_headers, create_meeting, put_transcript
    )
    await _processed_meeting(
        client, make_user, auth_headers, create_meeting, put_transcript, user=user
    )
    await _processed_meeting(
        client, make_user, auth_headers, create_meeting, put_transcript
    )  # someone else

    response = await client.get("/api/v1/action-items", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 6  # 3 items x my 2 meetings; the other user's 3 excluded
    deadlines = [i["deadline"] for i in body["items"]]
    assert deadlines == sorted(deadlines)  # soonest first


async def test_overdue_filter_uses_deadline_and_open_status(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, fake_llm
) -> None:
    today = datetime.now(UTC).date()
    extraction = platform_sync_extraction()
    fake_llm.extraction = MeetingExtraction(
        **{
            **extraction.model_dump(),
            "action_items": [
                ExtractedActionItem(
                    task="Past due",
                    owner="Priya",
                    deadline=str(today - timedelta(days=2)),
                    deadline_text="two days ago",
                    priority=None,
                    evidence_quote=None,
                ),
                ExtractedActionItem(
                    task="Also past, but done",
                    owner="Arjun",
                    deadline=str(today - timedelta(days=1)),
                    deadline_text="yesterday",
                    priority=None,
                    evidence_quote=None,
                ),
                ExtractedActionItem(
                    task="Not due yet",
                    owner="Meera",
                    deadline=str(today + timedelta(days=5)),
                    deadline_text="next week",
                    priority=None,
                    evidence_quote=None,
                ),
                ExtractedActionItem(
                    task="No deadline",
                    owner="Karthik",
                    deadline=None,
                    deadline_text=None,
                    priority=None,
                    evidence_quote=None,
                ),
            ],
        }
    )
    user = await make_user()
    headers = await auth_headers(user)
    # Meeting dated today so these deadlines pass the plausibility window.
    meeting = await create_meeting(headers, meeting_date=datetime.now(UTC).isoformat())
    await put_transcript(meeting["id"], headers, PLATFORM_SYNC)
    items = (
        await client.post(f"/api/v1/meetings/{meeting['id']}/process", headers=headers)
    ).json()["action_items"]

    done_id = next(i["id"] for i in items if i["task"] == "Also past, but done")
    await client.patch(f"/api/v1/action-items/{done_id}", json={"status": "done"}, headers=headers)

    overdue = (await client.get("/api/v1/action-items?overdue=true", headers=headers)).json()
    assert [i["task"] for i in overdue["items"]] == ["Past due"]
    assert overdue["items"][0]["is_overdue"] is True


async def test_status_filter(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    _, headers, _, body = await _processed_meeting(
        client, make_user, auth_headers, create_meeting, put_transcript
    )
    await client.patch(
        f"/api/v1/action-items/{body['action_items'][0]['id']}",
        json={"status": "done"},
        headers=headers,
    )

    done = (await client.get("/api/v1/action-items?status=done", headers=headers)).json()
    pending = (await client.get("/api/v1/action-items?status=pending", headers=headers)).json()
    assert done["total"] == 1
    assert pending["total"] == 2


async def test_patch_action_item_updates_fields_and_can_clear_deadline(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    _, headers, _, body = await _processed_meeting(
        client, make_user, auth_headers, create_meeting, put_transcript
    )
    item_id = body["action_items"][0]["id"]

    response = await client.patch(
        f"/api/v1/action-items/{item_id}",
        json={"status": "in_progress", "priority": "low", "deadline": None},
        headers=headers,
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["status"] == "in_progress"
    assert updated["priority"] == "low"
    assert updated["deadline"] is None  # explicit null clears a nullable field
    assert updated["task"] == body["action_items"][0]["task"]  # untouched


async def test_patch_action_item_rejects_null_for_required_fields(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    _, headers, _, body = await _processed_meeting(
        client, make_user, auth_headers, create_meeting, put_transcript
    )
    item_id = body["action_items"][0]["id"]

    for payload in ({"task": None}, {"status": None}, {"status": "finished"}):
        response = await client.patch(
            f"/api/v1/action-items/{item_id}", json=payload, headers=headers
        )
        assert response.status_code == 422, payload


async def test_other_user_cannot_list_or_modify_my_action_items_or_decisions(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    _, _, meeting, body = await _processed_meeting(
        client, make_user, auth_headers, create_meeting, put_transcript
    )
    intruder = await make_user()
    intruder_headers = await auth_headers(intruder)
    item_id = body["action_items"][0]["id"]
    decision_id = body["decisions"][0]["id"]

    assert (await client.get("/api/v1/action-items", headers=intruder_headers)).json()["total"] == 0
    # Filtering by my meeting id must not leak items either.
    leaked = await client.get(
        f"/api/v1/action-items?meeting_id={meeting['id']}", headers=intruder_headers
    )
    assert leaked.json()["total"] == 0

    patch_item = await client.patch(
        f"/api/v1/action-items/{item_id}", json={"status": "done"}, headers=intruder_headers
    )
    patch_decision = await client.patch(
        f"/api/v1/decisions/{decision_id}", json={"status": "resolved"}, headers=intruder_headers
    )
    assert patch_item.status_code == 404
    assert patch_decision.status_code == 404
    assert patch_item.json()["error"]["message"] == "Action item not found."


async def test_patch_decision_status(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    _, headers, meeting, body = await _processed_meeting(
        client, make_user, auth_headers, create_meeting, put_transcript
    )
    decision_id = body["decisions"][0]["id"]

    response = await client.patch(
        f"/api/v1/decisions/{decision_id}", json={"status": "resolved"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"

    null_status = await client.patch(
        f"/api/v1/decisions/{decision_id}", json={"status": None}, headers=headers
    )
    assert null_status.status_code == 422


async def test_deleting_a_meeting_cascades_to_its_intelligence(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    _, headers, meeting, body = await _processed_meeting(
        client, make_user, auth_headers, create_meeting, put_transcript
    )

    assert (
        await client.delete(f"/api/v1/meetings/{meeting['id']}", headers=headers)
    ).status_code == 204
    assert (await client.get("/api/v1/action-items", headers=headers)).json()["total"] == 0
    gone = await client.patch(
        f"/api/v1/action-items/{body['action_items'][0]['id']}",
        json={"status": "done"},
        headers=headers,
    )
    assert gone.status_code == 404
