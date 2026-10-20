"""Add betweenness_commit to graph_nodes.

Exact betweenness is now reused across small structural changes rather than
recomputed on every symbol-set change, so a node added since the last scoring
carries the column's 0.0 default without ever having been measured. Reporting
that as a centrality reads as "on no shortest path", which is the opposite
claim for a symbol just spliced into a hot call chain. NULL records "not
scored", the same omitted-reads-as-not-recorded contract as ``analyzed_commit``
on the health rows. The other metrics on this row are recomputed every run and
need no stamp.

Existing rows back-fill as NULL, which is correct: their stamp is genuinely
unknown until the next run scores them.

Local SQLite stores get the column from the model via ``init_db``'s additive
schema reconciler; this migration covers the managed Postgres ones.

Revision ID: 0061
Revises: 0060
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "graph_nodes",
        sa.Column("betweenness_commit", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("graph_nodes", "betweenness_commit")
