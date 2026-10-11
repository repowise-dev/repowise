"""Persist each refactoring plan's rank, rank factors and validation.

The finalizer ranks and validates every live plan once per index, so the plan
list endpoints read ``ORDER BY rank_position`` instead of ranking the whole
repository per request. NULL means the plan was not ranked by the last
finalize, and readers rank live.

Local SQLite stores get the columns from ``init_db``'s additive reconciler;
this migration covers managed Postgres.

Revision ID: 0101
Revises: 0100
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0101"
down_revision: str | None = "0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("refactoring_suggestions", sa.Column("rank_position", sa.Integer(), nullable=True))
    op.add_column("refactoring_suggestions", sa.Column("blast_size", sa.Integer(), nullable=True))
    op.add_column("refactoring_suggestions", sa.Column("rank_json", sa.Text(), nullable=True))
    op.create_index(
        "ix_refactoring_suggestions_repo_status_rank",
        "refactoring_suggestions",
        ["repository_id", "status", "rank_position"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_refactoring_suggestions_repo_status_rank", table_name="refactoring_suggestions"
    )
    op.drop_column("refactoring_suggestions", "rank_json")
    op.drop_column("refactoring_suggestions", "blast_size")
    op.drop_column("refactoring_suggestions", "rank_position")
