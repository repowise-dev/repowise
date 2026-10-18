"""Improved and moved totals, and move origins, on stored commit health.

``git_commit_health_deltas`` gains ``improved_count`` and ``moved_count``;
``git_commit_health_findings`` gains ``moved_from_path`` and
``moved_from_symbol`` for a finding whose code the commit moved from another
file. Rows scanned before this read 0 and NULL.

Local SQLite stores never run Alembic; ``init_db`` adds the columns there from
the model.

Revision ID: 0115
Revises: 0114
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0115"
down_revision: str | None = "0114"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ("improved_count", "moved_count"):
        op.add_column(
            "git_commit_health_deltas",
            sa.Column(name, sa.Integer, nullable=False, server_default="0"),
        )
    for name in ("moved_from_path", "moved_from_symbol"):
        op.add_column("git_commit_health_findings", sa.Column(name, sa.Text, nullable=True))


def downgrade() -> None:
    for name in ("moved_from_symbol", "moved_from_path"):
        op.drop_column("git_commit_health_findings", name)
    for name in ("moved_count", "improved_count"):
        op.drop_column("git_commit_health_deltas", name)
