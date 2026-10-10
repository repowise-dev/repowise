"""Persist ``graph_edges.deferred`` so lazy-import cycles stay suppressed.

An import inside a function body runs on first call, not at module load, which
is how code breaks an import cycle on purpose. Cycle detection drops these
edges, and a graph rehydrated from SQL must keep the mark.

Local SQLite stores get the column from ``init_db``'s additive reconciler; this
migration covers managed Postgres.

Revision ID: 0098
Revises: 0097
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0098"
down_revision: str | None = "0097"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "graph_edges",
        sa.Column(
            "deferred",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("graph_edges", "deferred")
