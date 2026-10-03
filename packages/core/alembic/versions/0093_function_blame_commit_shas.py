"""git_function_blame: keep each function's bounded commit set.

Split File's co-change edge needs the commits behind each function, which only
the transient blame index carried, so a re-score built a different partition
than the full index did. Nullable with no backfill: the next full index or
update writes it.

Local SQLite stores get the column from ``init_db``'s additive reconciler; this
migration covers managed Postgres.

Revision ID: 0093
Revises: 0092
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0093"
down_revision: str | None = "0092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_function_blame",
        sa.Column("commit_shas_json", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("git_function_blame", "commit_shas_json")
