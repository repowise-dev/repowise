"""git_metadata: rank prior_defect_count repo-relative.

Six months is a fixed window, so a repository landing many commits a day
accumulates more fix commits per file inside it than a quiet one, and an entry
gate of one fix then fires on a large share of the tree. This column ranks the
count over the repository, ties sharing a rank, so the gate is a share rather
than a fixed number of fixes.

Defaults to 0, which leaves ``prior_defect`` silent until the next index or
update repopulates it. Unlike the co-change breadth columns it is derivable
from data already stored, so ``recompute_git_percentiles`` fills it in without
a re-walk.

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres.

Revision ID: 0072
Revises: 0071
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_metadata",
        sa.Column("prior_defect_pct", sa.Float, nullable=False, server_default="0.0"),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "prior_defect_pct")
