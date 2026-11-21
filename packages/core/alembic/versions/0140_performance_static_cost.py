"""Persist ``performance_opportunities.static_cost``: the code's part of a cost.

``cost_proof`` reads ``proven`` both when the code shows how a loop's cost
scales and when a run measured it, so a reader that meant the static part could
not tell the two apart. ``static_cost`` (``worth.STATIC_COSTS``) holds what the
code alone shows; what a run measured stays in each row's stored facets.
``cost_proof`` keeps being written for readers that predate this column.

Existing rows are filled by ``fill_static_costs`` from their own marker and
stored facets, so no analysis reruns. The additive schema reconciler adds the
column and runs the same step for local SQLite stores; this migration covers
managed Postgres.

Revision ID: 0140
Revises: 0139
Create Date: 2026-11-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from repowise.core.persistence.crud.analysis.performance import fill_static_costs

# revision identifiers
revision: str = "0140"
down_revision: str | None = "0139"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "performance_opportunities",
        sa.Column("static_cost", sa.String(32), nullable=True),
    )
    fill_static_costs(op.get_bind())


def downgrade() -> None:
    op.drop_column("performance_opportunities", "static_cost")
