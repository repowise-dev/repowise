"""Persist ``graph_edges.type_only`` so type-only import cycles stay suppressed.

TypeScript ``import type`` and type-only re-exports are stripped by the compiler
and cannot cause runtime module evaluation deadlocks. Cycle detection drops
these edges to avoid reporting harmless type-level cycles.

Persisting ``type_only`` ensures that a graph rehydrated from SQL (e.g. during
incremental update or rescoring) maintains the type-only mark rather than
re-reporting false positive cycles.

Local SQLite stores get the column from ``init_db``'s additive reconciler; this
migration covers managed Postgres.

Revision ID: 0095
Revises: 0094
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0095"
down_revision: str | None = "0094"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "graph_edges",
        sa.Column(
            "type_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("graph_edges", "type_only")
