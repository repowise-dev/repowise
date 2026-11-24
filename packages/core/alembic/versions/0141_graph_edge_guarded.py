"""Persist ``graph_edges.guarded`` so import-guarded cycles stay suppressed.

A Python import in a ``try`` body with an ``except ImportError`` fallback runs
at module load, but the module already survives it failing, so it cannot close
an import cycle. Cycle detection drops these edges, and a graph rehydrated from
SQL must keep the mark.

Local SQLite stores get the column from ``init_db``'s additive reconciler; this
migration covers managed Postgres.

Revision ID: 0141
Revises: 0140
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0141"
down_revision: str | None = "0140"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "graph_edges",
        sa.Column(
            "guarded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("graph_edges", "guarded")
