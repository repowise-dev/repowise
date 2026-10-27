"""repositories: merge commits counted apart from ``total_commit_count``.

``total_commit_count`` was ``git rev-list --count HEAD``, merges included,
while every per-commit walk runs ``--no-merges``. On a repo that merges, the
stats page then read a 5,000-commit sample against a larger merge-inclusive
total and called a complete sample truncated. ``total_commit_count`` now means
non-merge commits reachable from HEAD, like every other commit count, and the
merges live in this column.

NULL until the next index writes it. ``init_db``'s additive schema reconciler
picks the column up from the model for local SQLite stores that never run
Alembic; this migration covers the managed Postgres ones.

Revision ID: 0087
Revises: 0086
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0087"
down_revision: str | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("total_merge_commit_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("repositories", "total_merge_commit_count")
