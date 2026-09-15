"""Ask your meetings (M8): minutes indexing, retrieval, grounding, attribution, isolation.

Real routes, database, pgvector, and job; the embedder is the bag-of-words
fake (texts sharing words are close) and the LLM is a fake whose answer can be
built from the prompt it receives, so tests control exactly what is cited.
"""

import re
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActionItem, ChunkSource, Decision, MeetingChunk
from app.schemas.ask import GroundedAnswer
from app.services.embeddings.search import SearchHit
from app.services.rag import (
    NO_MEETINGS_ANSWER,
    NOT_FOUND_ANSWER,
    UNSUPPORTED_ANSWER,
    select_hits,
    verify_citations,
)
from tests.fakes import PLATFORM_SYNC

ASK = "/api/v1/ask"

BLUEBIRD = """Nadia: The Bluebird acquisition price is confidential, we offered 38 million.
Omar: The Bluebird board meets on Thursday to review the offer.
Nadia: Keep the Bluebird acquisition out of every other meeting."""


async def _processed(
    client,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    *,
    user=None,
    content=PLATFORM_SYNC,
    **kw,
):
    user = user or await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(headers, **kw)
    await put_transcript(meeting["id"], headers, content)
    await process_and_wait(meeting["id"], headers)
    return user, headers, meeting


def _source_number(prompt: str, needle: str) -> int:
    """The [n] the prompt gave to the first source whose text contains ``needle``."""
    for match in re.finditer(
        r"\[(\d+)\] [^\n]*\n<<<SOURCE START>>>\n(.*?)\n<<<SOURCE END>>>", prompt, re.S
    ):
        if needle in match.group(2):
            return int(match.group(1))
    raise AssertionError(f"no source containing {needle!r} in prompt")


def _ask_calls(fake_llm) -> list[dict]:
    return [c for c in fake_llm.calls if c["schema"] == "GroundedAnswer"]


# ---------------------------------------------------------------------------
# Indexing the minutes
# ---------------------------------------------------------------------------


async def test_processing_indexes_the_minutes_with_references_to_their_records(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
) -> None:
    _, _, meeting = await _processed(
        client,
        make_user,
        auth_headers,
        create_meeting,
        put_transcript,
        process_and_wait,
        title="Platform sync",
    )
    mid = uuid.UUID(meeting["id"])
    rows = (
        await db_session.execute(
            select(MeetingChunk.source_kind, func.count())
            .where(MeetingChunk.meeting_id == mid)
            .group_by(MeetingChunk.source_kind)
        )
    ).all()
    assert dict(rows) == {
        ChunkSource.TRANSCRIPT: 3,
        ChunkSource.SUMMARY: 1,
        ChunkSource.DECISION: 2,
        ChunkSource.ACTION_ITEM: 3,
        ChunkSource.PENDING: 2,
        ChunkSource.NEXT_STEPS: 1,
    }

    action_chunks = (
        await db_session.scalars(
            select(MeetingChunk).where(
                MeetingChunk.meeting_id == mid, MeetingChunk.source_kind == ChunkSource.ACTION_ITEM
            )
        )
    ).all()
    item_ids = set(
        (await db_session.scalars(select(ActionItem.id).where(ActionItem.meeting_id == mid))).all()
    )
    assert {c.source_ref for c in action_chunks} == item_ids
    runbook = next(c for c in action_chunks if "runbook" in c.content)
    assert runbook.content == (
        "Platform sync. Action item: Prepare the production migration runbook. "
        "Owner: Karthik. Deadline: 2026-09-16."
    )
    assert runbook.char_start is None  # only transcript chunks have offsets
    assert "pending" not in runbook.content.lower()  # status is read live, never indexed

    decision_ids = set(
        (await db_session.scalars(select(Decision.id).where(Decision.meeting_id == mid))).all()
    )
    decision_refs = (
        await db_session.scalars(
            select(MeetingChunk.source_ref).where(
                MeetingChunk.meeting_id == mid, MeetingChunk.source_kind == ChunkSource.DECISION
            )
        )
    ).all()
    assert set(decision_refs) == decision_ids


async def test_minutes_are_reembedded_only_when_they_change(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_embedder,
    fake_llm,
) -> None:
    _, headers, meeting = await _processed(
        client, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    assert fake_embedder.document_calls == [3, 9]

    # A status change is not indexed content: nothing is re-embedded.
    items = (
        await client.get(f"/api/v1/meetings/{meeting['id']}/action-items", headers=headers)
    ).json()
    await client.patch(
        f"/api/v1/action-items/{items[0]['id']}", json={"status": "done"}, headers=headers
    )
    cached = await client.post(f"/api/v1/meetings/{meeting['id']}/process", headers=headers)
    assert cached.status_code == 200 and cached.json()["cached"] is True
    assert fake_embedder.document_calls == [3, 9]

    # A forced re-run that changes the minutes re-embeds them (and only them).
    fake_llm.extraction = fake_llm.extraction.model_copy(update={"next_steps": ["Retro on Monday"]})
    await process_and_wait(meeting["id"], headers, force=True)
    assert fake_embedder.document_calls == [3, 9, 9]


async def test_meetings_processed_before_m8_get_their_minutes_indexed_without_an_llm_call(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    worker,
    fake_llm,
) -> None:
    _, headers, meeting = await _processed(
        client, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    mid = uuid.UUID(meeting["id"])
    await db_session.execute(
        MeetingChunk.__table__.delete().where(
            MeetingChunk.meeting_id == mid, MeetingChunk.source_kind != ChunkSource.TRANSCRIPT
        )
    )
    await db_session.commit()
    calls = len(fake_llm.calls)

    submitted = await client.post(f"/api/v1/meetings/{meeting['id']}/process", headers=headers)
    assert submitted.status_code == 202  # not cached: the minutes index is missing
    await worker.run_once()

    job = (
        await client.get(f"/api/v1/jobs/{submitted.json()['job']['job_id']}", headers=headers)
    ).json()
    assert job["status"] == "COMPLETED" and job["result"]["minutes_chunks"] == 9
    assert len(fake_llm.calls) == calls


# ---------------------------------------------------------------------------
# Answers: grounding and source attribution
# ---------------------------------------------------------------------------


async def test_answer_cites_the_retrieved_minutes_with_their_meeting(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    _, headers, meeting = await _processed(
        client,
        make_user,
        auth_headers,
        create_meeting,
        put_transcript,
        process_and_wait,
        title="Platform sync",
    )

    def answer(prompt: str) -> GroundedAnswer:
        n = _source_number(prompt, "Action item: Prepare the production migration runbook")
        return GroundedAnswer(
            answerable=True,
            answer=f"Karthik owns the migration runbook, due 16 September [{n}].",
            cited_sources=[n],
        )

    fake_llm.answer = answer
    response = await client.post(
        ASK,
        json={"question": "Who is preparing the production migration runbook?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "answered"
    [source] = body["sources"]  # only what the answer cites, not everything retrieved
    assert (
        body["answer"]
        == f"Karthik owns the migration runbook, due 16 September [{source['number']}]."
    )
    assert source["meeting_id"] == meeting["id"]
    assert source["meeting_title"] == "Platform sync"
    assert source["kind"] == "action_item"
    assert "Owner: Karthik" in source["text"] and "Status: pending" in source["text"]
    assert body["retrieved"] > 1
    assert body["model"] == "fake-model-1" and body["prompt_version"] == "ask-v1"

    [call] = _ask_calls(fake_llm)
    assert "using only the numbered" in call["system"].lower()
    assert "Platform sync · 2026-09-10 · Minutes: action item" in call["prompt"]


async def test_sources_show_the_current_status_not_the_indexed_one(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    _, headers, meeting = await _processed(
        client, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    items = (
        await client.get(f"/api/v1/meetings/{meeting['id']}/action-items", headers=headers)
    ).json()
    runbook = next(i for i in items if "runbook" in i["task"])
    await client.patch(
        f"/api/v1/action-items/{runbook['id']}",
        json={"status": "done", "deadline": "2026-10-01"},
        headers=headers,
    )

    await client.post(
        ASK,
        json={"question": "Who is preparing the production migration runbook?"},
        headers=headers,
    )

    prompt = _ask_calls(fake_llm)[-1]["prompt"]
    n = _source_number(prompt, "Action item: Prepare the production migration runbook")
    block = prompt.split(f"[{n}] ", 1)[1].split("<<<SOURCE END>>>", 1)[0]
    assert "Deadline: 2026-10-01" in block and "Status: done" in block


async def test_citations_to_sources_that_were_not_shown_are_removed(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    _, headers, _ = await _processed(
        client, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    fake_llm.answer = GroundedAnswer(
        answerable=True,
        answer="Staging is on PostgreSQL 16 [1, 99]. Budget is 5M [42].",
        cited_sources=[1, 42],
    )

    body = (
        await client.post(
            ASK, json={"question": "Is staging migrated to PostgreSQL 16?"}, headers=headers
        )
    ).json()

    assert body["status"] == "answered"
    assert body["answer"] == "Staging is on PostgreSQL 16 [1]. Budget is 5M."
    assert [s["number"] for s in body["sources"]] == [1]


async def test_an_answer_that_cites_nothing_real_is_not_presented_as_answered(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    _, headers, _ = await _processed(
        client, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    fake_llm.answer = GroundedAnswer(
        answerable=True, answer="The migration is on Sunday [7].", cited_sources=[]
    )

    body = (
        await client.post(
            ASK, json={"question": "When is the production database migration?"}, headers=headers
        )
    ).json()

    assert body["status"] == "insufficient_context"
    assert body["answer"] == UNSUPPORTED_ANSWER
    assert body["sources"] == []


async def test_model_saying_the_sources_are_insufficient_is_respected(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    _, headers, _ = await _processed(
        client, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    fake_llm.answer = GroundedAnswer(
        answerable=False,
        answer="The meetings mention the migration but not its budget [2].",
        cited_sources=[2],
    )

    body = (
        await client.post(
            ASK, json={"question": "What is the production migration budget?"}, headers=headers
        )
    ).json()

    assert body["status"] == "insufficient_context"
    assert body["answer"] == "The meetings mention the migration but not its budget."
    assert body["sources"] == []


async def test_nothing_relevant_means_no_llm_call_and_no_invented_answer(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    _, headers, _ = await _processed(
        client, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )

    body = (
        await client.post(ASK, json={"question": "zebra xylophone saxophone"}, headers=headers)
    ).json()

    assert body["status"] == "insufficient_context"
    assert body["answer"] == NOT_FOUND_ANSWER
    assert body["sources"] == [] and body["model"] is None
    assert _ask_calls(fake_llm) == []


async def test_a_user_without_processed_meetings_is_told_so(
    client: AsyncClient, make_user, auth_headers, create_meeting, fake_llm
) -> None:
    headers = await auth_headers(await make_user())
    await create_meeting(headers)  # created, never processed

    body = (
        await client.post(ASK, json={"question": "What did we decide?"}, headers=headers)
    ).json()

    assert body["status"] == "no_indexed_meetings"
    assert body["answer"] == NO_MEETINGS_ANSWER
    assert fake_llm.calls == []


async def test_edited_transcripts_hide_their_old_minutes_from_answers(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    _, headers, meeting = await _processed(
        client, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    await put_transcript(
        meeting["id"], headers, "Leela: Today we only discussed the office picnic menu."
    )

    await client.post(
        ASK,
        json={"question": "Who is preparing the production migration runbook?"},
        headers=headers,
    )

    for call in _ask_calls(fake_llm):
        assert "runbook" not in call["prompt"]


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


async def test_another_users_meetings_never_reach_the_prompt_or_the_sources(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    alice, alice_headers, alice_meeting = await _processed(
        client,
        make_user,
        auth_headers,
        create_meeting,
        put_transcript,
        process_and_wait,
        content=BLUEBIRD,
        title="Weekly sync",
    )
    _, bob_headers, bob_meeting = await _processed(
        client,
        make_user,
        auth_headers,
        create_meeting,
        put_transcript,
        process_and_wait,
        title="Weekly sync",
    )
    question = {"question": "What price did we offer for the Bluebird acquisition?"}

    # Bob: Alice's meeting is the only relevant content, and he cannot see it.
    fake_llm.calls.clear()
    bob = (await client.post(ASK, json=question, headers=bob_headers)).json()
    # Whatever Bob's own meeting offers, Alice's content is absent.
    assert all(s["meeting_id"] == bob_meeting["id"] for s in bob["sources"])
    # Only the question mentions Bluebird; nothing from Alice's meeting is in the sources.
    for call in fake_llm.calls:
        sources_part = call["prompt"].split("<<<QUESTION START>>>")[0]
        assert "38 million" not in sources_part and "Nadia" not in sources_part
        assert "Bluebird" not in sources_part

    # Alice: the same question retrieves her meeting.
    def answer(prompt: str) -> GroundedAnswer:
        n = _source_number(prompt, "Bluebird acquisition price")
        return GroundedAnswer(answerable=True, answer=f"38 million [{n}].", cited_sources=[n])

    fake_llm.answer = answer
    mine = (await client.post(ASK, json=question, headers=alice_headers)).json()
    assert mine["status"] == "answered"
    assert {s["meeting_id"] for s in mine["sources"]} == {alice_meeting["id"]}
    assert all(bob_meeting["id"] not in c["prompt"] for c in fake_llm.calls)

    # Scoping to someone else's meeting is refused like every other meeting route.
    for meeting_id in (alice_meeting["id"], str(uuid.uuid4())):
        refused = await client.post(
            ASK, json={**question, "meeting_ids": [meeting_id]}, headers=bob_headers
        )
        assert refused.status_code == 404
    assert alice.id  # (alice's own scoped question works below)
    scoped = await client.post(
        ASK, json={**question, "meeting_ids": [alice_meeting["id"]]}, headers=alice_headers
    )
    assert scoped.status_code == 200 and scoped.json()["status"] == "answered"


async def test_scoping_limits_retrieval_to_the_chosen_meetings(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
) -> None:
    user, headers, first = await _processed(
        client,
        make_user,
        auth_headers,
        create_meeting,
        put_transcript,
        process_and_wait,
        title="First sync",
    )
    await _processed(
        client,
        make_user,
        auth_headers,
        create_meeting,
        put_transcript,
        process_and_wait,
        user=user,
        title="Second sync",
    )

    await client.post(
        ASK, json={"question": "migration runbook", "meeting_ids": [first["id"]]}, headers=headers
    )

    prompt = _ask_calls(fake_llm)[-1]["prompt"]
    assert "First sync ·" in prompt and "Second sync" not in prompt


async def test_ask_requires_authentication_and_validates_input(
    client: AsyncClient, make_user, auth_headers
) -> None:
    assert (await client.post(ASK, json={"question": "What did we decide?"})).status_code == 401
    headers = await auth_headers(await make_user())
    for body in (
        {},
        {"question": "hi"},
        {"question": "x" * 1001},
        {"question": "ok?", "meeting_ids": ["not-a-uuid"]},
    ):
        assert (await client.post(ASK, json=body, headers=headers)).status_code == 422, body


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------


def _hit(meeting: str, score: float) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(),
        meeting_id=uuid.UUID(int=int(meeting)),
        meeting_title=f"M{meeting}",
        meeting_date=datetime(2026, 9, 1, tzinfo=UTC),
        chunk_index=0,
        content="x",
        char_start=None,
        char_end=None,
        source_kind=ChunkSource.SUMMARY,
        source_ref=None,
        score=score,
    )


def test_select_hits_applies_the_score_gate_the_per_meeting_cap_and_top_k() -> None:
    hits = [
        _hit("1", 0.9),
        _hit("1", 0.8),
        _hit("1", 0.7),
        _hit("2", 0.6),
        _hit("3", 0.5),
        _hit("4", 0.1),
    ]
    chosen = select_hits(hits, top_k=3, max_per_meeting=2, min_score=0.2)
    assert [(h.meeting_title, h.score) for h in chosen] == [("M1", 0.9), ("M1", 0.8), ("M2", 0.6)]
    assert select_hits(hits, top_k=10, max_per_meeting=10, min_score=0.95) == []
    assert len(select_hits(hits, top_k=10, max_per_meeting=10, min_score=0.2)) == 5


@pytest.mark.parametrize(
    ("text", "listed", "count", "expected_text", "expected_cited"),
    [
        ("A [1]. B [2].", [1, 2], 2, "A [1]. B [2].", [1, 2]),
        ("A [2, 1].", [], 2, "A [2][1].", [2, 1]),
        ("A [0] [3].", [0, 3], 2, "A.", []),
        ("A [1].", [2], 2, "A [1].", [1, 2]),
        ("No markers.", [], 3, "No markers.", []),
    ],
)
def test_verify_citations(text, listed, count, expected_text, expected_cited) -> None:
    answer = GroundedAnswer(answerable=True, answer=text, cited_sources=listed)
    assert verify_citations(answer, count) == (expected_text, expected_cited)
