"""minutes of meeting fields and notes input

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-14

M7 (core MOM workflow):

* ``summaries`` gains the Minutes of Meeting content produced by prompt
  extract-v2: keywords, speaker contributions, unresolved items, next steps.
  Existing rows default to empty lists; their prompt version (extract-v1) marks
  them as not current, so re-processing fills them in.
* ``transcripts.source`` accepts ``notes``: meeting notes or a written
  description typed by a person. Autogenerate does not detect CHECK changes, so
  the constraint is replaced explicitly.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON_COLUMNS = ("keywords", "speakers", "unresolved_items", "next_steps")
# op.f(): the name is final; the naming convention must not prefix it again.
_SOURCE_CHECK = op.f("ck_transcripts_transcript_source")


def upgrade() -> None:
    for column in _JSON_COLUMNS:
        op.add_column(
            "summaries",
            sa.Column(
                column,
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            ),
        )
    op.drop_constraint(_SOURCE_CHECK, "transcripts", type_="check")
    op.create_check_constraint(
        _SOURCE_CHECK, "transcripts", "source IN ('manual', 'transcription', 'notes')"
    )


def downgrade() -> None:
    # Notes are typed text, so they survive a downgrade as manual transcripts.
    op.execute("UPDATE transcripts SET source = 'manual' WHERE source = 'notes'")
    op.drop_constraint(_SOURCE_CHECK, "transcripts", type_="check")
    op.create_check_constraint(
        _SOURCE_CHECK, "transcripts", "source IN ('manual', 'transcription')"
    )
    for column in reversed(_JSON_COLUMNS):
        op.drop_column("summaries", column)
