"""Add Phase 3 git intelligence columns to git_metadata.

New signals: rename tracking (original_path) and merge commit
frequency (merge_commit_count_90d).

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_metadata",
        sa.Column("original_path", sa.Text, nullable=True),
    )
    op.add_column(
        "git_metadata",
        sa.Column("merge_commit_count_90d", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "merge_commit_count_90d")
    op.drop_column("git_metadata", "original_path")
