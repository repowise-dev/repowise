"""repositories: whole-history git totals (commit count, age, contributors, founder).

The per-commit ``git_commits`` table is bounded to the newest N commits (churn
and co-change analysis need no deeper history), so the stats page derived
project age, total-commit count, and contributor count from that sample and
reported a multi-year repo as a few months old with a few hundred commits and
only its recent authors (issue #730).

Adds four nullable repo-level columns the git indexer fills with cheap
``git rev-list``/``shortlog`` calls at index time: ``total_commit_count``,
``first_commit_at`` (root commit date, i.e. project age), ``first_commit_author``
(the founder), and ``total_contributor_count`` (all-time mailmap-folded authors).
Nullable so existing rows and non-git indexes degrade gracefully until the next
index writes them.

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("total_commit_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "repositories",
        sa.Column("first_commit_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "repositories",
        sa.Column("total_contributor_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "repositories",
        sa.Column("first_commit_author", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("repositories", "first_commit_author")
    op.drop_column("repositories", "total_contributor_count")
    op.drop_column("repositories", "first_commit_at")
    op.drop_column("repositories", "total_commit_count")
