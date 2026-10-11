"""Record the commit each health metric row was scored against.

``_meta`` already reports ``indexed_commit`` and ``health_analyzed_at``, but
nothing said *which commit* the health scores describe. Health runs as its own
pass and can lag the index, so ``repositories.head_commit`` does not answer it:
a response could show a current index next to scores computed several commits
earlier and look entirely fresh.

Per-row rather than per-repo because the incremental path
(``upsert_health_metrics``) rewrites only the files that changed, so the table
legitimately holds rows from several passes at once. ``get_health`` reports the
newest pass's commit plus a count of how many others are still represented,
instead of picking one and implying the whole table agrees.

NULL on every row written before this column existed, which reads as "not
recorded" — the field is simply omitted from ``_meta`` rather than guessed at.

``init_db``'s additive schema reconciler picks the column up from the model for
local SQLite stores that never run Alembic; this migration covers the managed
Postgres ones.

Revision ID: 0050
Revises: 0049
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "health_file_metrics",
        sa.Column("analyzed_commit", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("health_file_metrics", "analyzed_commit")
