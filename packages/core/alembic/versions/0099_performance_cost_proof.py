"""Persist ``performance_opportunities.cost_proof`` so the default queue can filter it.

A cause whose loop nobody measured to grow is ``unproven``: it never leads and
leaves the default performance queue, listed under its own filter. The queue
filters in SQL, so the verdict is a column. Rows written before it read
``proven`` until the next analysis rewrites them.

Local SQLite stores get the column from ``init_db``'s additive reconciler; this
migration covers managed Postgres.

Revision ID: 0099
Revises: 0098
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0099"
down_revision: str | None = "0098"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "performance_opportunities",
        sa.Column("cost_proof", sa.String(16), nullable=False, server_default="proven"),
    )


def downgrade() -> None:
    op.drop_column("performance_opportunities", "cost_proof")
