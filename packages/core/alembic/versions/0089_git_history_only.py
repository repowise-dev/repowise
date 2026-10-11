"""git_metadata: history-tier rows for non-code files.

Non-code tracked files (docs, config, data) now get their commit counts, first
and last commit and authors from the repo-wide walk. ``history_only`` marks
those rows so the churn, hotspot, entropy, scatter and prior-defect rankings
stay over code files. Existing rows are all code rows, hence ``false``.

Revision ID: 0089
Revises: 0088
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0089"
down_revision: str | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_metadata",
        sa.Column("history_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "history_only")
