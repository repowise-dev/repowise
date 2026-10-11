"""``fix_events``: one row per bug-fix commit x file, with SZZ candidates.

``prior_defect_count`` is a bare number; this table is the evidence under it.
Each row carries what a fix commit did to one file (shape kind, the old-side
line ranges it replaced, changed LOC) and the ranked commits ``git blame`` puts
on those lines at ``fix^``.

``committed_at`` is stored undecayed on purpose: every recency weight is derived
at read time, so changing a half-life never requires a reindex.

New indexes only — no existing table is touched, so a pre-0040 wiki.db upgrades
without rewriting anything and reads an empty table until the next index.

Revision ID: 0040
Revises: 0039
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fix_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fix_sha", sa.String(40), nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("shape_kind", sa.String(16), nullable=False, server_default="code_fix"),
        sa.Column("old_ranges_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("changed_loc", sa.Integer, nullable=False, server_default="0"),
        sa.Column("inducing_shas_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("taxonomy_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_id", "fix_sha", "file_path", name="uq_fix_event"),
    )
    op.create_index("ix_fix_events_repo_path", "fix_events", ["repository_id", "file_path"])
    op.create_index("ix_fix_events_repo_time", "fix_events", ["repository_id", "committed_at"])


def downgrade() -> None:
    op.drop_index("ix_fix_events_repo_time", table_name="fix_events")
    op.drop_index("ix_fix_events_repo_path", table_name="fix_events")
    op.drop_table("fix_events")
