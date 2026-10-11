"""Add change_entropy + change_entropy_pct columns to git_metadata.

Stores Hassan's History Complexity Metric (decay-weighted per-commit change
scatter) and its repo-wide percentile, computed during the FULL-tier
co-change walk and consumed by the ``change_entropy`` health biomarker.

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_metadata",
        sa.Column("change_entropy", sa.Float, nullable=False, server_default="0.0"),
    )
    op.add_column(
        "git_metadata",
        sa.Column("change_entropy_pct", sa.Float, nullable=False, server_default="0.0"),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "change_entropy_pct")
    op.drop_column("git_metadata", "change_entropy")
