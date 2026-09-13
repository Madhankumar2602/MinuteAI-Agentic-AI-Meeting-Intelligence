"""add enum CHECK constraints and the queued meeting status

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-13

Migrations 0001 and 0002 declared enum columns as VARCHAR intending a CHECK
constraint, but SQLAlchemy 2.0 only emits that constraint when
``create_constraint=True``. The database therefore accepted any string (verified:
``status = 'nonsense'`` inserted successfully). This migration adds the missing
constraints for all six enum columns, and includes the new ``queued`` meeting
status introduced by M3's background processing.

Constraint names follow the metadata naming convention (``ck_<table>_<enum>``),
so they match what the models now generate.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, enum name, column, allowed values)
CHECKS: list[tuple[str, str, str, list[str]]] = [
    ("meetings", "meeting_source_type", "source_type", ["text", "audio", "video"]),
    (
        "meetings",
        "meeting_status",
        "status",
        ["created", "queued", "processing", "completed", "failed"],
    ),
    ("transcripts", "transcript_source", "source", ["manual", "transcription"]),
    ("decisions", "decision_status", "status", ["open", "resolved", "superseded"]),
    ("action_items", "action_item_priority", "priority", ["low", "medium", "high"]),
    (
        "action_items",
        "action_item_status",
        "status",
        ["pending", "in_progress", "done", "cancelled"],
    ),
]


def upgrade() -> None:
    for table, enum_name, column, values in CHECKS:
        allowed = ", ".join(f"'{v}'" for v in values)
        # NULL passes a CHECK (it evaluates to unknown), so the nullable
        # action_items.priority column still accepts "no priority".
        op.create_check_constraint(
            op.f(f"ck_{table}_{enum_name}"), table, f"{column} IN ({allowed})"
        )


def downgrade() -> None:
    # Rows already moved to 'queued' would violate a recreated pre-M3 check, so
    # reset them before dropping; with no constraint at all they are then valid.
    op.execute("UPDATE meetings SET status = 'created' WHERE status = 'queued'")
    for table, enum_name, _column, _values in reversed(CHECKS):
        op.drop_constraint(op.f(f"ck_{table}_{enum_name}"), table, type_="check")
