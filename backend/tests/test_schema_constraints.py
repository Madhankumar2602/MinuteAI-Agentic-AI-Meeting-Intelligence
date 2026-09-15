"""Database-level integrity, independent of the application code.

Regression for a real gap: until migration 0003 the enum columns had no CHECK
constraint, and PostgreSQL accepted status = 'nonsense'. These tests write
directly with SQL, bypassing Pydantic and the ORM, to prove the database itself
now refuses invalid values.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


async def _insert_meeting(db_session, **overrides) -> None:
    user_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, password_hash, full_name, is_active) "
            "VALUES (:id, :email, 'h', 'n', true)"
        ),
        {"id": user_id, "email": f"{user_id}@example.com"},
    )
    values = {"source_type": "text", "status": "created", **overrides}
    await db_session.execute(
        text(
            "INSERT INTO meetings (id, owner_id, title, meeting_date, source_type, status) "
            "VALUES (:id, :owner, 't', now(), :source_type, :status)"
        ),
        {"id": uuid.uuid4(), "owner": user_id, **values},
    )


@pytest.mark.parametrize(
    "overrides",
    [{"status": "nonsense"}, {"source_type": "fax"}],
    ids=["invalid-status", "invalid-source-type"],
)
async def test_database_rejects_invalid_enum_values(db_session, overrides) -> None:
    with pytest.raises(IntegrityError, match="ck_meetings_"):
        await _insert_meeting(db_session, **overrides)


async def test_database_accepts_the_new_queued_status(db_session) -> None:
    await _insert_meeting(db_session, status="queued")


async def test_all_enum_columns_have_check_constraints(db_session) -> None:
    rows = await db_session.execute(
        text("SELECT conname FROM pg_constraint WHERE contype = 'c' AND conname LIKE 'ck_%'")
    )
    assert {r[0] for r in rows} == {
        "ck_meetings_meeting_source_type",
        "ck_meetings_meeting_status",
        "ck_transcripts_transcript_source",
        "ck_decisions_decision_status",
        "ck_action_items_action_item_priority",
        "ck_action_items_action_item_status",
        "ck_meeting_chunks_chunk_source",
    }
