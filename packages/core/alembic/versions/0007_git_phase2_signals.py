"""Add Phase 2 git intelligence columns to git_metadata.

New signals: diff size (lines added/deleted), commit message classification,
recent ownership, and bus factor.

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Diff size
    op.add_column(
        "git_metadata",
        sa.Column("lines_added_90d", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "git_metadata",
        sa.Column("lines_deleted_90d", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "git_metadata",
        sa.Column("avg_commit_size", sa.Float, nullable=False, server_default="0.0"),
    )

    # Commit classification
    op.add_column(
        "git_metadata",
        sa.Column("commit_categories_json", sa.Text, nullable=False, server_default="{}"),
    )

    # Recent ownership & bus factor
    op.add_column(
        "git_metadata",
        sa.Column("recent_owner_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "git_metadata",
        sa.Column("recent_owner_commit_pct", sa.Float, nullable=True),
    )
    op.add_column(
        "git_metadata",
        sa.Column("bus_factor", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "git_metadata",
        sa.Column("contributor_count", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "contributor_count")
    op.drop_column("git_metadata", "bus_factor")
    op.drop_column("git_metadata", "recent_owner_commit_pct")
    op.drop_column("git_metadata", "recent_owner_name")
    op.drop_column("git_metadata", "commit_categories_json")
    op.drop_column("git_metadata", "avg_commit_size")
    op.drop_column("git_metadata", "lines_deleted_90d")
    op.drop_column("git_metadata", "lines_added_90d")
