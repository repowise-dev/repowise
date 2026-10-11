"""Persist ``performance_opportunities.execution_role`` so the default queue can filter it.

The role (request, event consumer, scheduled job, startup, CLI, tooling, test or
unknown) says what runs a cause's loop. The queue leaves startup, CLI, tooling
and test loops out in SQL, so the role is a column. Rows written before it read
``unknown`` until the next analysis rewrites them.

Local SQLite stores get the column from ``init_db``'s additive reconciler; this
migration covers managed Postgres.

Revision ID: 0102
Revises: 0101
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0102"
down_revision: str | None = "0101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "performance_opportunities",
        sa.Column("execution_role", sa.String(16), nullable=False, server_default="unknown"),
    )


def downgrade() -> None:
    op.drop_column("performance_opportunities", "execution_role")
