"""Indexing through the job, semantic search through the API, and access control."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Meeting, MeetingChunk, Transcript
from app.services.embeddings.chunking import CHUNKER_VERSION
from app.services.embeddings.search import semantic_search
from tests.fakes import PLATFORM_SYNC, FakeEmbedder

SEARCH = "/api/v1/search"


async def _processed_meeting(
    make_user, auth_headers, create_meeting, put_transcript, process_and_wait, *, user=None, **kw
):
    user = user or await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(headers, **kw)
    await put_transcript(meeting["id"], headers, PLATFORM_SYNC)
    await process_and_wait(meeting["id"], headers)
    return user, headers, meeting


async def _chunk_count(db: AsyncSession, meeting_id: str) -> int:
    return await db.scalar(
        select(func.count()).where(MeetingChunk.meeting_id == uuid.UUID(meeting_id))
    )


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


async def test_processing_indexes_the_transcript_as_exact_slices(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_embedder: FakeEmbedder,
) -> None:
    _, _, meeting = await _processed_meeting(
        make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    chunks = (
        await db_session.scalars(
            select(MeetingChunk)
            .where(MeetingChunk.meeting_id == uuid.UUID(meeting["id"]))
            .order_by(MeetingChunk.chunk_index)
        )
    ).all()
    transcript = await db_session.scalar(
        select(Transcript).where(Transcript.meeting_id == uuid.UUID(meeting["id"]))
    )

    assert len(chunks) == 3
    for chunk in chunks:
        assert transcript.content[chunk.char_start : chunk.char_end] == chunk.content
        assert chunk.transcript_sha256 == transcript.content_sha256
        assert chunk.embedding_model == fake_embedder.model
        assert chunk.chunker_version == CHUNKER_VERSION
        assert len(chunk.embedding) == 384
        # Stored as float4 by pgvector, so compare at float32 precision.
        assert list(chunk.embedding) == pytest.approx(fake_embedder.vector(chunk.content), abs=1e-6)


async def test_cached_submission_still_indexes_a_meeting_processed_before_m6(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
    fake_llm,
    worker,
) -> None:
    _, headers, meeting = await _processed_meeting(
        make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    # Simulate a meeting whose extraction predates the search index.
    await db_session.execute(
        MeetingChunk.__table__.delete().where(MeetingChunk.meeting_id == uuid.UUID(meeting["id"]))
    )
    await db_session.commit()
    llm_calls = len(fake_llm.calls)

    submitted = await client.post(f"/api/v1/meetings/{meeting['id']}/process", headers=headers)
    assert submitted.status_code == 202  # not "cached": the index is missing
    await worker.run_once()

    job = (
        await client.get(f"/api/v1/jobs/{submitted.json()['job']['job_id']}", headers=headers)
    ).json()
    assert job["status"] == "COMPLETED"
    assert job["result"]["cached"] is True
    assert job["result"]["chunks"] == 3
    assert len(fake_llm.calls) == llm_calls  # no second LLM call
    assert await _chunk_count(db_session, meeting["id"]) == 3

    # Now everything is current: answered from cache without a job.
    again = await client.post(f"/api/v1/meetings/{meeting['id']}/process", headers=headers)
    assert again.status_code == 200 and again.json()["cached"] is True


async def test_chunks_are_deleted_with_their_meeting(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    process_and_wait,
) -> None:
    _, headers, meeting = await _processed_meeting(
        make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    assert await _chunk_count(db_session, meeting["id"]) == 3

    assert (
        await client.delete(f"/api/v1/meetings/{meeting['id']}", headers=headers)
    ).status_code == 204
    assert await _chunk_count(db_session, meeting["id"]) == 0


async def test_embedding_failure_is_retried_and_extraction_waits(
    client: AsyncClient,
    make_user,
    auth_headers,
    create_meeting,
    put_transcript,
    worker,
    fake_embedder: FakeEmbedder,
    fake_llm,
    clock,
) -> None:
    from app.services.embeddings.base import EmbeddingUnavailableError

    user = await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(headers)
    await put_transcript(meeting["id"], headers, PLATFORM_SYNC)
    job_id = (
        await client.post(f"/api/v1/meetings/{meeting['id']}/process", headers=headers)
    ).json()["job"]["job_id"]

    fake_embedder.error = EmbeddingUnavailableError()
    await worker.run_once()
    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "QUEUED"
    assert job["error"]["code"] == "embedding_unavailable"
    assert fake_llm.calls == []

    fake_embedder.error = None
    clock.advance(31)
    await worker.run_once()
    job = (await client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "COMPLETED"
    assert job["result"]["chunks"] == 3


# ---------------------------------------------------------------------------
# Search API
# ---------------------------------------------------------------------------


async def test_search_ranks_the_matching_passage_first(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
) -> None:
    _, headers, meeting = await _processed_meeting(
        make_user,
        auth_headers,
        create_meeting,
        put_transcript,
        process_and_wait,
        title="Platform sync",
    )

    response = await client.get(
        SEARCH, params={"q": "quarterly infrastructure cost report finance"}, headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == FakeEmbedder.model
    results = body["results"]
    assert len(results) == 3
    top = results[0]
    assert "quarterly infrastructure cost report" in top["content"]
    assert top["meeting_id"] == meeting["id"]
    assert top["meeting_title"] == "Platform sync"
    assert PLATFORM_SYNC[top["char_start"] : top["char_end"]] == top["content"]
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert -1.0 <= scores[-1] <= scores[0] <= 1.0


async def test_search_never_returns_another_users_meetings(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
) -> None:
    # Two users with identical transcripts: identical vectors, identical scores.
    _, alice_headers, alice_meeting = await _processed_meeting(
        make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    _, bob_headers, bob_meeting = await _processed_meeting(
        make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )

    for headers, own in ((alice_headers, alice_meeting), (bob_headers, bob_meeting)):
        results = (
            await client.get(SEARCH, params={"q": "runbook", "limit": 50}, headers=headers)
        ).json()["results"]
        assert results
        assert {r["meeting_id"] for r in results} == {own["id"]}


async def test_search_can_be_scoped_to_one_accessible_meeting(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
) -> None:
    user, headers, first = await _processed_meeting(
        make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    _, _, second = await _processed_meeting(
        make_user, auth_headers, create_meeting, put_transcript, process_and_wait, user=user
    )
    _, _, someone_elses = await _processed_meeting(
        make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )

    unscoped = (
        await client.get(SEARCH, params={"q": "runbook", "limit": 50}, headers=headers)
    ).json()
    assert {r["meeting_id"] for r in unscoped["results"]} == {first["id"], second["id"]}

    scoped = await client.get(
        SEARCH, params={"q": "runbook", "meeting_id": second["id"]}, headers=headers
    )
    assert {r["meeting_id"] for r in scoped.json()["results"]} == {second["id"]}

    # Someone else's meeting and a meeting that does not exist look the same.
    for meeting_id in (someone_elses["id"], str(uuid.uuid4())):
        denied = await client.get(
            SEARCH, params={"q": "runbook", "meeting_id": meeting_id}, headers=headers
        )
        assert denied.status_code == 404
        assert denied.json()["error"]["code"] == "not_found"


async def test_edited_transcript_hides_old_chunks_until_reprocessed(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript, process_and_wait
) -> None:
    _, headers, meeting = await _processed_meeting(
        make_user, auth_headers, create_meeting, put_transcript, process_and_wait
    )
    await put_transcript(
        meeting["id"], headers, "Leela: The only topic today was the office picnic menu."
    )

    stale = (await client.get(SEARCH, params={"q": "runbook"}, headers=headers)).json()
    assert stale["results"] == []  # old text is no longer in the transcript

    await process_and_wait(meeting["id"], headers)
    fresh = (await client.get(SEARCH, params={"q": "picnic menu"}, headers=headers)).json()
    assert [r["content"] for r in fresh["results"]] == [
        "Leela: The only topic today was the office picnic menu."
    ]


async def test_unprocessed_meetings_are_simply_not_found(
    client: AsyncClient, make_user, auth_headers, create_meeting, put_transcript
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    meeting = await create_meeting(headers)
    await put_transcript(meeting["id"], headers, PLATFORM_SYNC)

    response = await client.get(SEARCH, params={"q": "runbook"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["results"] == []


async def test_search_requires_authentication_and_a_sensible_query(
    client: AsyncClient, make_user, auth_headers
) -> None:
    assert (await client.get(SEARCH, params={"q": "runbook"})).status_code == 401

    headers = await auth_headers(await make_user())
    for params in (
        {},
        {"q": "x"},
        {"q": "y" * 501},
        {"q": "runbook", "limit": 0},
        {"q": "ok", "limit": 51},
    ):
        response = await client.get(SEARCH, params=params, headers=headers)
        assert response.status_code == 422, params


# ---------------------------------------------------------------------------
# The HNSW filtering problem (ADR 0002)
# ---------------------------------------------------------------------------


async def _insert_chunks(db: AsyncSession, owner, vectors: list[list[float]], model: str) -> None:
    meeting = Meeting(owner_id=owner.id, title="Bulk", meeting_date=func.now())
    db.add(meeting)
    await db.flush()
    db.add(
        Transcript(
            meeting_id=meeting.id, content="x", content_sha256="0" * 64, char_count=1, word_count=1
        )
    )
    db.add_all(
        MeetingChunk(
            meeting_id=meeting.id,
            chunk_index=i,
            content=f"chunk {i}",
            char_start=0,
            char_end=1,
            token_count=2,
            transcript_sha256="0" * 64,
            embedding_model=model,
            chunker_version=CHUNKER_VERSION,
            embedding=v,
        )
        for i, v in enumerate(vectors)
    )
    await db.commit()


def _unit(i: int, noise: float = 0.0) -> list[float]:
    v = [0.0] * 384
    v[i] = 1.0
    v[(i + 1) % 384] = noise
    norm = (1 + noise * noise) ** 0.5
    return [x / norm for x in v]


# Same shape as semantic_search's statement, for EXPLAIN and the control run.
_RAW_SEARCH = (
    "SELECT c.id FROM meeting_chunks c JOIN meetings m ON m.id = c.meeting_id "
    "JOIN transcripts t ON t.meeting_id = c.meeting_id "
    "AND t.content_sha256 = c.transcript_sha256 "
    "WHERE m.owner_id = :owner AND c.embedding_model = :model "
    "ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT 3"
)


async def test_filtered_hnsw_search_still_returns_the_users_results(
    db_session: AsyncSession, make_user
) -> None:
    """300 of another user's chunks sit right next to the query; ours are further away.

    At this size PostgreSQL would normally filter by owner first and sort
    exactly, so the planner is steered onto the HNSW index, which is what it
    chooses for a user with many meetings. A plain HNSW scan then returns only
    the nearest candidates, all of them someone else's, and filtering leaves
    the user with nothing. Iterative scanning keeps searching until it has
    enough rows the user may see.
    """
    model = "hnsw-test-model"
    alice, bob = await make_user(), await make_user()
    await _insert_chunks(db_session, bob, [_unit(0, noise=i / 1000) for i in range(300)], model)
    await _insert_chunks(db_session, alice, [_unit(0, noise=5.0 + i) for i in range(3)], model)
    query = _unit(0)
    params = {"owner": alice.id, "model": model, "q": str(query)}

    for setting in ("enable_seqscan = off", "enable_sort = off", "enable_bitmapscan = off"):
        await db_session.execute(text(f"SET LOCAL {setting}"))
    plan = "\n".join((await db_session.execute(text("EXPLAIN " + _RAW_SEARCH), params)).scalars())
    assert "Index Scan using ix_meeting_chunks_embedding_hnsw" in plan

    hits = await semantic_search(
        db_session, owner_id=alice.id, query_vector=query, model=model, limit=3
    )
    assert len(hits) == 3

    # Control: the same scan without iterative scanning really does lose the
    # results, so the assertion above is not passing by accident.
    await db_session.execute(text("SET LOCAL hnsw.iterative_scan = off"))
    await db_session.execute(text("SET LOCAL hnsw.ef_search = 40"))
    assert (await db_session.execute(text(_RAW_SEARCH), params)).all() == []
