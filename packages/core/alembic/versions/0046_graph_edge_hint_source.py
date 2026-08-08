"""Persist ``graph_edges.hint_source`` so cohesion survives rehydration.

Several resolver passes synthesise file-level edges to record that two files
belong to one compilation unit — Go package siblings, JVM same-package classes,
C# ``partial`` fragments and global usings, Swift same-module files, and the
C/C++ header/implementation pair. Each pass stamps ``hint_source`` on the edge.

Cycle detection reads that stamp to drop those edges: files in one compilation
unit cannot depend on each other, so counting them turns every cohesive package
into a fabricated import cycle (issue #1294).

The stamp only lived in the in-memory NetworkX graph. Anything running against a
graph rehydrated from ``graph_edges`` — the health engine's break-cycle detector,
incremental updates — saw cohesion edges as ordinary imports and re-reported the
false cycles. Persisting the column closes that gap.

Nullable with no default: NULL means "a real import/using directive", which is
the correct reading for every pre-existing row.

``init_db``'s additive schema reconciler picks the column up from the model for
local SQLite stores that never run Alembic; this migration covers the managed
Postgres ones. The two converge on the same shape.

Revision ID: 0046
Revises: 0045
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "graph_edges",
        sa.Column("hint_source", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("graph_edges", "hint_source")
