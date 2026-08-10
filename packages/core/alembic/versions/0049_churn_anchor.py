"""Anchor lifetime churn so an update folds instead of re-walking the history.

``repositories.total_lines_added`` / ``total_lines_deleted`` (0043) were
recomputed on every update by ``git log --shortstat`` over the entire history —
the one O(history) call on the update path, 2.1s on a 2.2k-commit checkout and
growing linearly with age, to move a total by one commit's worth of lines.

``churn_anchor_sha`` records the commit those totals were computed at, so the
next capture can add only ``anchor..HEAD``. It is written only alongside a churn
figure, and only believed when the next capture re-proves both that it is still
an ancestor of HEAD (rebase, force-push, branch swap) and that
``prior_count + count(anchor..HEAD)`` equals the current commit count (a shallow
clone that has since been deepened keeps the anchor an ancestor while adding
history *below* it). Either check failing falls back to the full walk, so the
stored totals can go stale only by being recomputed correctly.

NULL on every index written before this, which costs one full walk and then
anchors itself.

``init_db``'s additive schema reconciler picks the column up from the model for
local SQLite stores that never run Alembic; this migration covers the managed
Postgres ones.

Revision ID: 0049
Revises: 0048
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("churn_anchor_sha", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("repositories", "churn_anchor_sha")
