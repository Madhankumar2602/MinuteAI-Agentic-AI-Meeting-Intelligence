"""chunks from minutes of meeting

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-15

M8 (RAG): ``meeting_chunks`` also holds passages embedded from the Minutes of
Meeting (summary, decisions, action items, pending items, next steps), in the
same table and index as transcript passages.

* ``source_kind`` says what a chunk was built from; existing rows are transcript
  chunks. CHECK constraint named by convention (autogenerate does not emit it).
* ``source_ref`` points at the decision / action item a chunk describes.
* Character offsets only exist for transcript chunks, so they become nullable.
* Chunk numbering is per source kind.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("transcript", "summary", "decision", "action_item", "pending", "next_steps")


def upgrade() -> None:
    op.add_column(
        "meeting_chunks",
        sa.Column("source_kind", sa.String(length=16), server_default="transcript", nullable=False),
    )
    op.add_column("meeting_chunks", sa.Column("source_ref", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        op.f("ck_meeting_chunks_chunk_source"),
        "meeting_chunks",
        "source_kind IN (" + ", ".join(f"'{k}'" for k in KINDS) + ")",
    )
    op.alter_column("meeting_chunks", "char_start", existing_type=sa.Integer(), nullable=True)
    op.alter_column("meeting_chunks", "char_end", existing_type=sa.Integer(), nullable=True)
    op.drop_constraint(
        op.f("uq_meeting_chunks_meeting_id_chunk_index"), "meeting_chunks", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_meeting_chunks_meeting_id_source_kind_chunk_index"),
        "meeting_chunks",
        ["meeting_id", "source_kind", "chunk_index"],
    )


def downgrade() -> None:
    # Minutes chunks have no place in the M6 schema; they are rebuilt on upgrade.
    op.execute("DELETE FROM meeting_chunks WHERE source_kind <> 'transcript'")
    op.drop_constraint(
        op.f("uq_meeting_chunks_meeting_id_source_kind_chunk_index"),
        "meeting_chunks",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_meeting_chunks_meeting_id_chunk_index"),
        "meeting_chunks",
        ["meeting_id", "chunk_index"],
    )
    op.alter_column("meeting_chunks", "char_end", existing_type=sa.Integer(), nullable=False)
    op.alter_column("meeting_chunks", "char_start", existing_type=sa.Integer(), nullable=False)
    op.drop_constraint(op.f("ck_meeting_chunks_chunk_source"), "meeting_chunks", type_="check")
    op.drop_column("meeting_chunks", "source_ref")
    op.drop_column("meeting_chunks", "source_kind")
