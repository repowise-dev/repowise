"""Add commit_count_capped column to git_metadata.

Tracks whether the per-file commit count was truncated at the configured
commit_limit.  Downstream tools use this to surface a warning when the
total is approximate.

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "git_metadata",
        sa.Column("commit_count_capped", sa.Boolean, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "commit_count_capped")
