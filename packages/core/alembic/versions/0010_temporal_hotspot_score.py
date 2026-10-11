"""Add temporal_hotspot_score column to git_metadata.

Stores an exponentially time-decayed churn score used as the primary
signal for hotspot percentile ranking (PERCENT_RANK window function).

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_metadata",
        sa.Column(
            "temporal_hotspot_score",
            sa.Float,
            nullable=True,
            server_default="0.0",
        ),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "temporal_hotspot_score")
