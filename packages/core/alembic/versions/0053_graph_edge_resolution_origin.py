"""Record which resolution strategy produced a call edge.

Call resolution runs a cascade of strategies whose confidences overlap: a
same-file match and a self/this match both score 0.95, and a repo-wide unique
name scores 0.50 — a guess that execution-flow tracing admits at exactly the
threshold it clears. Once an edge reached the graph, which strategy minted it
was unrecoverable, so a consumer could not tell a certainty from a guess.

``graph_edges.resolution_origin`` carries one name from the closed
``ResolutionOrigin`` vocabulary. NULL means the row predates the vocabulary or
the edge is not resolver-produced; consumers omit the field rather than showing
it as unknown, so no re-index is required.

``init_db``'s additive schema reconciler picks the column up from the model for
local SQLite stores that never run Alembic; this migration covers the managed
Postgres ones.

Revision ID: 0053
Revises: 0052
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "graph_edges",
        sa.Column("resolution_origin", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("graph_edges", "resolution_origin")
