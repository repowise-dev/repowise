"""Stats page: first-commit subject, lifetime churn, and per-commit UTC offset.

Three captures the Stats page needs that the existing tables cannot answer.

``repositories.first_commit_subject`` — the root commit's headline, so the page
can render the repo's opening line rather than just its date. Rides along free
with the root commit already loaded for ``first_commit_at`` (0037).

``repositories.total_lines_added`` / ``total_lines_deleted`` — lifetime churn.
Summing ``git_commits`` cannot answer this: that table is bounded to the newest
N commits, so a repo with more history than the window would silently report a
windowed figure as a lifetime one. NULL when the history was too deep to walk.

``git_commits.committed_offset_minutes`` — minutes east of UTC at commit time.
``committed_at`` is stored as a UTC instant, which discards the author's local
wall-clock, so time-of-day analysis (the punch card, per-author peak hour) read
a 10pm commit in Mumbai as a mid-afternoon one. Backfilled on the next update
(``pipeline.incremental.reconcile_commit_offsets``) rather than requiring a
re-index; rows stay NULL and fall back to UTC until then.

All nullable so existing indexes keep working untouched.

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("first_commit_subject", sa.Text(), nullable=True),
    )
    op.add_column(
        "repositories",
        sa.Column("total_lines_added", sa.Integer(), nullable=True),
    )
    op.add_column(
        "repositories",
        sa.Column("total_lines_deleted", sa.Integer(), nullable=True),
    )
    op.add_column(
        "git_commits",
        sa.Column("committed_offset_minutes", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("git_commits", "committed_offset_minutes")
    op.drop_column("repositories", "total_lines_deleted")
    op.drop_column("repositories", "total_lines_added")
    op.drop_column("repositories", "first_commit_subject")
