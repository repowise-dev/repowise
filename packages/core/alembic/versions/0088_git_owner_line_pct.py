"""git_metadata: the primary owner's line share, apart from their commit share.

With blame, the indexer made the top blame author the primary owner and then
overwrote ``primary_owner_commit_pct`` with that author's share of current
lines, so pages read "X, at 51% of commits" where 51% was a line share.
``primary_owner_commit_pct`` goes back to the owner's own share of commits and
the blame share moves to ``primary_owner_line_pct``.

NULL without blame. Existing rows blame wrote are moved here by
``split_blame_line_shares``, since a file with no new commits is never
re-walked. The additive schema reconciler adds the column and runs the same
step for local SQLite stores; this migration covers managed Postgres.

Revision ID: 0088
Revises: 0087
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from repowise.core.persistence.crud.git import split_blame_line_shares

# revision identifiers
revision: str = "0088"
down_revision: str | None = "0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_metadata",
        sa.Column("primary_owner_line_pct", sa.Float(), nullable=True),
    )
    split_blame_line_shares(op.get_bind())


def downgrade() -> None:
    op.drop_column("git_metadata", "primary_owner_line_pct")
