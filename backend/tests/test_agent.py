"""The follow-up agent (M9): detection, drafting guardrails, approval, safety.

Real routes, database, pgvector, and processing job. The embedder is the
bag-of-words fake and the model is a fake whose drafts can be built from the
prompt it receives, so tests control exactly what the "model" says and check
that code, not the model, decides what becomes a proposal.

The platform-sync fixture meeting (10 Sep 2026) produces:
  * "Fix the clock synchronisation..."   Meera,   due 2026-09-11, high priority
  * "Prepare the production migration..." Karthik, due 2026-09-16
  * "Send the quarterly infrastructure..." Arjun,   due 2026-09-30
  * two open decisions and two unresolved items.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agent import get_today
from app.core.config import settings
from app.db.models import (
    ActionItem,
    ActionItemStatus,
    AgentRun,
    AgentRunStatus,
    FollowUpProposal,
    ProposalKind,
)
from app.main import app
from app.services.agent.detectors import find_candidates, find_topic_candidates
from app.services.agent.drafting import AgentDrafts, DraftedFollowUp
from app.services.embeddings.base import EmbeddingUnavailableError
from app.services.llm.base import LLMUnavailableError
from tests.fakes import agent_candidates

AGENT = "/api/v1/agent"
STEPS = ["observe", "deduplicate", "prioritise", "recall", "decide_and_draft", "verify", "propose"]


@pytest.fixture
def today(client: AsyncClient):
    """Fix the agent's "today"; call ``today.set(date)`` to move it."""

    class Today:
        value = date(2026, 9, 20)

        def set(self, value: date) -> None:
            self.value = value

    current = Today()
    app.dependency_overrides[get_today] = lambda: current.value
    return current


@pytest.fixture
def processed(make_user, auth_headers, create_meeting, put_transcript, process_and_wait):
    from tests.fakes import PLATFORM_SYNC

    async def _processed(user=None, **kw):
        user = user or await make_user()
        headers = await auth_headers(user)
        meeting = await create_meeting(headers, **kw)
        await put_transcript(meeting["id"], headers, PLATFORM_SYNC)
        await process_and_wait(meeting["id"], headers)
        return user, headers, meeting

    return _processed


def _agent_calls(fake_llm) -> list[dict]:
    return [c for c in fake_llm.calls if c["schema"] == "AgentDrafts"]


async def _run(client: AsyncClient, headers: dict) -> dict:
    response = await client.post(f"{AGENT}/runs", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _proposals(client: AsyncClient, headers: dict, **params) -> dict:
    response = await client.get(f"{AGENT}/proposals", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _step(run: dict, name: str) -> dict:
    return next(s for s in run["steps"] if s["step"] == name)


# ---------------------------------------------------------------------------
# A run end to end
# ---------------------------------------------------------------------------


async def test_run_detects_follow_ups_and_proposes_drafts_without_changing_meetings(
    client: AsyncClient, db_session: AsyncSession, processed, today, fake_llm
) -> None:
    _, headers, meeting = await processed(title="Platform sync")
    fake_llm.calls.clear()

    run = await _run(client, headers)

    assert run["status"] == "completed"
    assert [s["step"] for s in run["steps"]] == STEPS
    assert _step(run, "observe")["found"] == {"overdue_action": 2, "unresolved_topic": 2}
    assert run["candidates_found"] == 4 and run["proposals_created"] == 4
    assert run["model"] == "fake-model-1" and run["used_fallback"] is False
    # One model call drafts every candidate.
    assert len(_agent_calls(fake_llm)) == 1

    listing = await _proposals(client, headers)
    assert listing["pending"] == 4
    by_kind = {}
    for p in listing["items"]:
        by_kind.setdefault(p["kind"], []).append(p)
        assert p["status"] == "proposed" and p["drafted_by"] == "ai"
        assert p["meeting_id"] == meeting["id"] and p["meeting_title"] == "Platform sync"
        assert p["run_id"] == run["id"]
        assert all(s["meeting_id"] == meeting["id"] for s in p["sources"])

    overdue = {p["title"]: p for p in by_kind["overdue_action"]}
    clock = overdue["Overdue by 9 days: Fix the clock synchronisation on the API servers"]
    assert clock["recipients"] == ["Meera"] and clock["action_item_id"]
    assert "Overdue by 4 days: Prepare the production migration runbook" in overdue
    topics = {p["title"] for p in by_kind["unresolved_topic"]}
    assert topics == {
        "Unresolved: Whether to move to a different auth provider",
        "Unresolved: Update the deployment documentation (no owner)",
    }
    # Topic follow-ups may address anyone who was in the meeting.
    assert set(by_kind["unresolved_topic"][0]["recipients"]) <= {
        "Arjun",
        "Karthik",
        "Meera",
        "Priya",
    }

    # The agent only proposes: meeting data is untouched.
    statuses = (
        await db_session.scalars(
            select(ActionItem.status).where(ActionItem.meeting_id == uuid.UUID(meeting["id"]))
        )
    ).all()
    assert set(statuses) == {ActionItemStatus.PENDING}

    dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
    assert dashboard["follow_ups_pending"] == 4

    runs = (await client.get(f"{AGENT}/runs", headers=headers)).json()
    assert [r["id"] for r in runs] == [run["id"]]


async def test_the_prompt_carries_facts_recipients_and_recalled_sources(
    client: AsyncClient, processed, today, fake_llm
) -> None:
    _, headers, _ = await processed()
    await _run(client, headers)

    call = _agent_calls(fake_llm)[0]
    assert "TODAY: 2026-09-20" in call["prompt"] and "SENDER: Test User" in call["prompt"]
    parsed = agent_candidates(call["prompt"])
    assert list(parsed) == ["C1", "C2", "C3", "C4"]
    # Most urgent first: 9 days late before 4 days late, action items before topics.
    assert "Days overdue: 9" in parsed["C1"]["text"] and parsed["C1"]["recipients"] == ["Meera"]
    assert "Days overdue: 4" in parsed["C2"]["text"]
    assert "KIND: unresolved topic" in parsed["C3"]["text"]
    # Each candidate is given recalled passages to ground its draft.
    assert all(1 <= len(c["sources"]) <= settings.agent_context_passages for c in parsed.values())
    assert "untrusted" in call["system"].lower()


async def test_nothing_to_do_means_no_model_call(
    client: AsyncClient, make_user, auth_headers, today, fake_llm
) -> None:
    headers = await auth_headers(await make_user())
    run = await _run(client, headers)
    assert run["status"] == "completed"
    assert run["candidates_found"] == 0 and run["proposals_created"] == 0
    assert run["model"] is None and _agent_calls(fake_llm) == []


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


async def test_detectors_cover_due_soon_unassigned_and_stale_decisions(
    db_session: AsyncSession, processed, today, fake_embedder
) -> None:
    user, _, meeting = await processed()
    mid = uuid.UUID(meeting["id"])
    # Take the owner and deadline off the cost-report item: it becomes "unassigned".
    await db_session.execute(
        update(ActionItem)
        .where(ActionItem.meeting_id == mid, ActionItem.task.like("Send the quarterly%"))
        .values(owner_name=None, deadline=None, deadline_text=None)
    )
    await db_session.commit()

    # 13 Sep: runbook due in 3 days (outside the 2-day window), clock fix overdue.
    found = await find_candidates(
        db_session, owner_id=user.id, today=date(2026, 9, 14), embedder=fake_embedder
    )
    kinds = [c.kind for c in found]
    assert kinds.count(ProposalKind.OVERDUE_ACTION) == 1
    assert kinds.count(ProposalKind.DUE_SOON_ACTION) == 1
    assert kinds.count(ProposalKind.UNASSIGNED_ACTION) == 1
    # Decisions are only "stale" 14 days after the meeting.
    assert ProposalKind.OPEN_DECISION not in kinds

    due_soon = next(c for c in found if c.kind == ProposalKind.DUE_SOON_ACTION)
    assert due_soon.title == "Due in 2 days: Prepare the production migration runbook"
    unassigned = next(c for c in found if c.kind == ProposalKind.UNASSIGNED_ACTION)
    assert unassigned.allowed_recipients == ["Arjun", "Karthik", "Meera", "Priya"]
    # Urgency ordering: overdue, due soon, then unassigned.
    assert kinds.index(ProposalKind.OVERDUE_ACTION) < kinds.index(ProposalKind.DUE_SOON_ACTION)
    assert kinds.index(ProposalKind.DUE_SOON_ACTION) < kinds.index(ProposalKind.UNASSIGNED_ACTION)

    later = await find_candidates(
        db_session, owner_id=user.id, today=date(2026, 9, 25), embedder=fake_embedder
    )
    decisions = [c for c in later if c.kind == ProposalKind.OPEN_DECISION]
    assert len(decisions) == 2 and all(c.decision_id for c in decisions)
    assert all(c.allowed_recipients for c in decisions)

    # Done and cancelled work is never chased.
    await db_session.execute(
        update(ActionItem).where(ActionItem.meeting_id == mid).values(status=ActionItemStatus.DONE)
    )
    await db_session.commit()
    done = await find_candidates(
        db_session, owner_id=user.id, today=date(2026, 9, 25), embedder=fake_embedder
    )
    assert not [c for c in done if c.action_item_id]


async def test_a_topic_raised_in_two_meetings_is_one_recurring_candidate(
    db_session: AsyncSession, make_user, processed, today, fake_embedder
) -> None:
    user = await make_user()
    _, _, older = await processed(
        user, title="Platform sync", meeting_date=datetime(2026, 9, 3, tzinfo=UTC).isoformat()
    )
    _, _, newer = await processed(
        user, title="Platform sync 2", meeting_date=datetime(2026, 9, 17, tzinfo=UTC).isoformat()
    )

    topics = await find_topic_candidates(
        db_session, owner_id=user.id, today=date(2026, 9, 20), embedder=fake_embedder
    )
    assert len(topics) == 2
    assert {c.kind for c in topics} == {ProposalKind.RECURRING_TOPIC}
    # Reported once, on the newest meeting, naming where else it came up.
    assert {str(c.meeting_id) for c in topics} == {newer["id"]}
    assert all(c.related_meetings == ["Platform sync · 2026-09-03"] for c in topics)
    assert all(c.title.startswith("Raised in 2 meetings: ") for c in topics)

    later = await find_topic_candidates(
        db_session,
        owner_id=user.id,
        today=date(2026, 9, 3) + timedelta(days=settings.agent_lookback_days + 1),
        embedder=fake_embedder,
    )
    # Outside the look-back window the older meeting is no longer examined
    # itself, though it still counts as history for the newer one.
    assert len(later) == 2
    assert {str(c.meeting_id) for c in later} == {newer["id"]}
    assert older["id"] != newer["id"]


# ---------------------------------------------------------------------------
# Guardrails on what the model says
# ---------------------------------------------------------------------------


def _draft(cid: str, **kw) -> DraftedFollowUp:
    base = dict(
        candidate_id=cid,
        follow_up=True,
        priority="medium",
        rationale="Needs attention.",
        recipients=[],
        subject=f"About {cid}",
        message=f"Hi, checking in on {cid}.",
        cited_sources=[],
    )
    return DraftedFollowUp(**{**base, **kw})


async def test_guardrails_constrain_the_models_drafts(
    client: AsyncClient, processed, today, fake_llm
) -> None:
    _, headers, _ = await processed()

    def drafts(prompt: str) -> AgentDrafts:
        parsed = agent_candidates(prompt)
        assert parsed["C4"]["sources"], "C4 needs a recalled passage to cite"
        return AgentDrafts(
            drafts=[
                # Recipients outside the allowed list and an unshown source are dropped.
                _draft(
                    "C1",
                    priority="high",
                    recipients=["meera", "Mallory"],
                    rationale="It is late [1][9].",
                    cited_sources=[1, 9],
                    subject="Clock fix [1]",
                ),
                _draft("C1", subject="A second draft for the same candidate"),
                _draft("C99", subject="An invented candidate"),
                # "Already resolved" without evidence is not believed.
                _draft("C2", follow_up=False),
                # Empty text falls back to the template.
                _draft("C3", message="   "),
                # C4: resolved, with evidence -> no proposal.
                _draft("C4", follow_up=False, rationale="Settled [1].", cited_sources=[1]),
            ]
        )

    fake_llm.agent_drafts = drafts
    run = await _run(client, headers)

    assert run["proposals_created"] == 3
    verify = _step(run, "verify")
    assert "ignored a draft for unknown candidate 'C99'" in verify["notes"]
    assert "ignored a second draft for C1" in verify["notes"]
    assert any("Mallory" in a for a in verify["adjustments"]["C1"])
    assert any("without evidence" in a for a in verify["adjustments"]["C2"])
    assert any("template" in a for a in verify["adjustments"]["C3"])
    assert len(verify["resolved"]) == 1
    assert _step(run, "propose")["skipped_as_resolved"] == 1

    items = {p["title"]: p for p in (await _proposals(client, headers))["items"]}
    clock = next(p for t, p in items.items() if "clock" in t)
    assert clock["recipients"] == ["Meera"]  # canonical spelling, Mallory removed
    assert clock["priority"] == "high"
    assert clock["draft_subject"] == "Clock fix"
    assert clock["rationale"] == "It is late [1]."
    assert [s["number"] for s in clock["sources"]] == [1]
    runbook = next(p for t, p in items.items() if "runbook" in t)
    assert runbook["status"] == "proposed"
    templated = [p for p in items.values() if p["drafted_by"] == "template"]
    assert len(templated) == 1 and templated[0]["kind"] == "unresolved_topic"
    assert templated[0]["draft_body"].endswith("Thanks,\nTest User")
    # The candidate resolved with evidence was not proposed.
    assert not any("deployment documentation" in t for t in items)


async def test_model_failure_falls_back_to_labelled_template_drafts(
    client: AsyncClient, processed, today, fake_llm
) -> None:
    _, headers, _ = await processed()
    fake_llm.error = LLMUnavailableError()

    run = await _run(client, headers)
    assert run["status"] == "completed"
    assert run["used_fallback"] is True and run["model"] is None
    drafting = _step(run, "decide_and_draft")
    assert drafting["fallback"] == "template" and drafting["error"] == "llm_unavailable"

    items = (await _proposals(client, headers))["items"]
    assert len(items) == 4 and {p["drafted_by"] for p in items} == {"template"}
    clock = next(p for p in items if "clock" in p["title"])
    assert clock["recipients"] == ["Meera"]
    assert clock["draft_body"].startswith("Hi Meera,")
    assert "was due 2026-09-11" in clock["draft_body"]


async def test_an_unexpected_failure_marks_the_run_failed_and_frees_the_user(
    client: AsyncClient, processed, today, fake_embedder
) -> None:
    _, headers, _ = await processed()
    fake_embedder.error = EmbeddingUnavailableError()

    run = await _run(client, headers)
    assert run["status"] == "failed" and run["error_code"] == fake_embedder.error.code
    assert run["steps"][-1]["step"] == "failed"
    assert (await _proposals(client, headers))["items"] == []

    fake_embedder.error = None
    assert (await _run(client, headers))["status"] == "completed"


async def test_the_candidate_budget_keeps_the_most_urgent(
    client: AsyncClient, processed, today, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "agent_max_candidates", 2)
    _, headers, _ = await processed()

    run = await _run(client, headers)
    assert run["candidates_found"] == 4 and run["proposals_created"] == 2
    assert _step(run, "prioritise")["deferred"] == 2
    items = (await _proposals(client, headers))["items"]
    assert {p["kind"] for p in items} == {"overdue_action"}

    # The deferred ones are picked up by the next run.
    run = await _run(client, headers)
    assert run["proposals_created"] == 2
    assert {p["kind"] for p in (await _proposals(client, headers))["items"]} == {
        "overdue_action",
        "unresolved_topic",
    }


# ---------------------------------------------------------------------------
# Never the same follow-up twice
# ---------------------------------------------------------------------------


async def test_a_situation_is_proposed_once_even_after_rejection(
    client: AsyncClient, db_session: AsyncSession, processed, today, fake_llm
) -> None:
    _, headers, meeting = await processed()
    await _run(client, headers)
    items = (await _proposals(client, headers))["items"]
    clock = next(p for p in items if "clock" in p["title"])
    reject = await client.post(
        f"{AGENT}/proposals/{clock['id']}/reject", json={"note": "Not needed"}, headers=headers
    )
    assert reject.status_code == 200

    fake_llm.calls.clear()
    run = await _run(client, headers)
    assert run["proposals_created"] == 0
    assert _step(run, "deduplicate")["already_proposed"] == 4
    assert _agent_calls(fake_llm) == []  # nothing new, so the model is not called

    # A new deadline is a new situation.
    await client.patch(
        f"/api/v1/action-items/{clock['action_item_id']}",
        json={"deadline": "2026-09-15"},
        headers=headers,
    )
    run = await _run(client, headers)
    assert run["proposals_created"] == 1
    newest = (await _proposals(client, headers, status="proposed"))["items"]
    assert any(p["title"] == "Overdue by 5 days: Fix the clock synchronisation on the API servers"
               for p in newest)  # fmt: skip


# ---------------------------------------------------------------------------
# Human approval
# ---------------------------------------------------------------------------


async def test_approve_with_edits_reject_and_decide_only_once(
    client: AsyncClient, processed, make_user, auth_headers, today
) -> None:
    _, headers, _ = await processed()
    await _run(client, headers)
    first, second, third, *_ = (await _proposals(client, headers))["items"]

    edited = await client.post(
        f"{AGENT}/proposals/{first['id']}/approve",
        json={"body": "  My own words.  ", "note": "sent by email"},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["status"] == "approved" and body["decided_at"]
    assert body["final_body"] == "My own words."
    assert body["final_subject"] == first["draft_subject"]
    assert body["draft_body"] == first["draft_body"]  # the original draft is kept
    assert body["decision_note"] == "sent by email"

    as_drafted = await client.post(
        f"{AGENT}/proposals/{second['id']}/approve", json={}, headers=headers
    )
    assert as_drafted.json()["final_body"] == second["draft_body"]

    rejected = await client.post(
        f"{AGENT}/proposals/{third['id']}/reject", json={}, headers=headers
    )
    assert rejected.json()["status"] == "rejected" and rejected.json()["final_body"] is None

    again = await client.post(f"{AGENT}/proposals/{first['id']}/reject", json={}, headers=headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "proposal_already_decided"

    listing = await _proposals(client, headers)
    assert listing["pending"] == 1
    assert len((await _proposals(client, headers, status="approved"))["items"]) == 2

    # Someone else cannot see or decide these proposals.
    stranger = await auth_headers(await make_user())
    assert (await _proposals(client, stranger)) == {"items": [], "pending": 0}
    for action in ("approve", "reject"):
        response = await client.post(
            f"{AGENT}/proposals/{listing['items'][0]['id']}/{action}", json={}, headers=stranger
        )
        assert response.status_code == 404


async def test_approval_rejects_blank_edits(client: AsyncClient, processed, today) -> None:
    _, headers, _ = await processed()
    await _run(client, headers)
    proposal = (await _proposals(client, headers))["items"][0]
    response = await client.post(
        f"{AGENT}/proposals/{proposal['id']}/approve", json={"body": ""}, headers=headers
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Isolation, single flight, lifecycle
# ---------------------------------------------------------------------------


async def test_a_run_only_sees_its_owners_meetings(
    client: AsyncClient, processed, make_user, auth_headers, today, fake_llm
) -> None:
    await processed()  # someone else's overdue work
    mine = await auth_headers(await make_user())
    run = await _run(client, mine)
    assert run["candidates_found"] == 0 and _agent_calls(fake_llm) == []

    _, headers, meeting = await processed()
    fake_llm.calls.clear()
    run = await _run(client, headers)
    assert run["candidates_found"] == 4
    prompt = _agent_calls(fake_llm)[0]["prompt"]
    # Recalled sources come only from this user's meeting.
    items = (await _proposals(client, headers))["items"]
    assert {s["meeting_id"] for p in items for s in p["sources"]} == {meeting["id"]}
    assert prompt.count("=== CANDIDATE") == 4


async def test_one_run_at_a_time_and_interrupted_runs_are_recovered(
    client: AsyncClient, db_session: AsyncSession, make_user, auth_headers, today
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    stuck = AgentRun(
        owner_id=user.id,
        trigger="manual",
        status=AgentRunStatus.RUNNING,
        prompt_version="agent-v1",
        steps=[],
    )
    db_session.add(stuck)
    await db_session.commit()
    stuck_id = stuck.id

    busy = await client.post(f"{AGENT}/runs", headers=headers)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "agent_run_in_progress"

    # Another user is not blocked.
    assert (await _run(client, await auth_headers(await make_user())))["status"] == "completed"

    # A run left "running" by a restart stops blocking after a while.
    await db_session.execute(
        update(AgentRun)
        .where(AgentRun.id == stuck_id)
        .values(started_at=datetime.now(UTC) - timedelta(minutes=11))
    )
    await db_session.commit()
    assert (await _run(client, headers))["status"] == "completed"
    stuck = await db_session.get(AgentRun, stuck_id, populate_existing=True)
    assert stuck.status == AgentRunStatus.FAILED and stuck.error_code == "interrupted"


async def test_proposals_follow_their_meeting(
    client: AsyncClient, db_session: AsyncSession, processed, today, process_and_wait
) -> None:
    _, headers, meeting = await processed()
    await _run(client, headers)

    # Reprocessing replaces the action items; proposals keep their text but
    # lose the link to the old record.
    await process_and_wait(meeting["id"], headers, force=True)
    items = (await _proposals(client, headers))["items"]
    assert len(items) == 4
    assert all(p["action_item_id"] is None for p in items)

    deleted = await client.delete(f"/api/v1/meetings/{meeting['id']}", headers=headers)
    assert deleted.status_code == 204
    assert (await _proposals(client, headers)) == {"items": [], "pending": 0}
    remaining = await db_session.scalar(
        select(FollowUpProposal.id).where(FollowUpProposal.meeting_id == uuid.UUID(meeting["id"]))
    )
    assert remaining is None
