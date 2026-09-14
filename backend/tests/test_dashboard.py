"""Dashboard summary."""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.schemas.extraction import ExtractedActionItem, MeetingExtraction
from tests.fakes import PLATFORM_SYNC, platform_sync_extraction


async def test_empty_dashboard(client: AsyncClient, make_user, auth_headers) -> None:
    user = await make_user()
    body = (await client.get("/api/v1/dashboard", headers=await auth_headers(user))).json()

    assert body["meetings"]["total"] == 0
    assert set(body["meetings"]["by_status"]) == {
        "created",
        "queued",
        "processing",
        "completed",
        "failed",
    }
    assert body["action_items"] == {"open": 0, "overdue": 0, "due_soon": 0, "done": 0}
    assert body["recent_meetings"] == []
    assert body["attention"] == []


async def test_dashboard_counts_and_attention_list(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    today = datetime.now(UTC).date()
    item = lambda task, days, owner="Priya": ExtractedActionItem(  # noqa: E731
        task=task,
        owner=owner,
        deadline=str(today + timedelta(days=days)) if days is not None else None,
        deadline_text=None,
        priority=None,
        evidence_quote=None,
    )
    fake_llm.extraction = MeetingExtraction(
        **{
            **platform_sync_extraction().model_dump(),
            "action_items": [
                item("Overdue task", -3),
                item("Due in two days", 2),
                item("Due next month", 40),
                item("No deadline", None),
                item("Done already", -1, owner="Arjun"),
            ],
        }
    )
    user = await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(
        headers, title="Weekly sync", meeting_date=datetime.now(UTC).isoformat()
    )
    await create_meeting(headers, title="Untouched meeting")
    await put_transcript(meeting["id"], headers, PLATFORM_SYNC)
    items = (await process_and_wait(meeting["id"], headers))["action_items"]
    done_id = next(i["id"] for i in items if i["task"] == "Done already")
    await client.patch(f"/api/v1/action-items/{done_id}", json={"status": "done"}, headers=headers)

    # Another user's data must not leak into the counts.
    other = await make_user()
    await create_meeting(await auth_headers(other), title="Someone else's")

    body = (await client.get("/api/v1/dashboard", headers=headers)).json()

    assert body["meetings"]["total"] == 2
    assert body["meetings"]["by_status"]["completed"] == 1
    assert body["meetings"]["by_status"]["created"] == 1
    assert body["action_items"] == {"open": 4, "overdue": 1, "due_soon": 1, "done": 1}
    assert [m["title"] for m in body["recent_meetings"]][0] in {"Weekly sync", "Untouched meeting"}
    assert [a["task"] for a in body["attention"]] == ["Overdue task", "Due in two days"]
    assert body["attention"][0]["meeting_title"] == "Weekly sync"
    assert body["attention"][0]["is_overdue"] is True


async def test_cross_meeting_action_items_include_meeting_title(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(
        headers, title="Platform sync", meeting_date="2026-09-10T10:00:00+00:00"
    )
    await put_transcript(meeting["id"], headers, PLATFORM_SYNC)
    await process_and_wait(meeting["id"], headers)

    cross = (await client.get("/api/v1/action-items", headers=headers)).json()["items"]
    per_meeting = (
        await client.get(f"/api/v1/meetings/{meeting['id']}/action-items", headers=headers)
    ).json()

    assert {i["meeting_title"] for i in cross} == {"Platform sync"}
    assert {i["meeting_title"] for i in per_meeting} == {None}


async def test_dashboard_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/dashboard")).status_code == 401
