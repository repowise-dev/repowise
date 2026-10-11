"""Persist each unit's default-queue verdict: eligible, reason, value, tier.

Findings, performance opportunities and refactoring opportunities are judged
once per index by ``analysis.health.queue``, so default lists filter and
count on a column instead of re-deriving the rule per request. NULL means the
unit was not judged yet; the next index writes it.

Local SQLite stores get the columns from ``init_db``'s additive reconciler;
this migration covers managed Postgres.

Revision ID: 0103
Revises: 0102
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0103"
down_revision: str | None = "0102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("health_findings", "performance_opportunities", "refactoring_opportunities")
_INDEXES = (
    (
        "ix_performance_opportunities_repo_status_queue",
        "performance_opportunities",
        ["repository_id", "status", "queue_eligible", "rank_position"],
    ),
    (
        "ix_refactoring_opportunities_repo_status_eligible",
        "refactoring_opportunities",
        ["repository_id", "status", "queue_eligible", "queue_position"],
    ),
    (
        "ix_health_findings_repo_status_queue",
        "health_findings",
        ["repository_id", "status", "queue_eligible"],
    ),
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("queue_eligible", sa.Boolean(), nullable=True))
        op.add_column(table, sa.Column("queue_reason", sa.String(32), nullable=True))
        op.add_column(table, sa.Column("queue_value", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("queue_tier", sa.String(8), nullable=True))
    for name, table, columns in _INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table, _columns in _INDEXES:
        op.drop_index(name, table_name=table)
    # Batch mode rebuilds the table on SQLite, which cannot drop a column in place.
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            for column in ("queue_tier", "queue_value", "queue_reason", "queue_eligible"):
                batch.drop_column(column)
