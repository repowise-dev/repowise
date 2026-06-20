"""Per-dimension health scores on health_file_metrics + dimension on findings.

The single code-health score is split into three orthogonal signals at the
scoring layer: defect, maintainability, and performance. This revision lands the
storage for that split:

- ``health_file_metrics`` gains nullable ``defect_score`` / ``maintainability_score``
  / ``performance_score`` columns. The existing ``score`` column stays the
  overall surfaced number and equals ``defect_score`` for now.
- ``health_findings`` gains a nullable ``dimension`` column (which pillar a
  finding homes under) for later per-pillar filtering.

All columns are additive and nullable with no backfill - the next index recompute
repopulates them. The overall surfaced score is unchanged.

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("health_file_metrics", sa.Column("defect_score", sa.Float, nullable=True))
    op.add_column(
        "health_file_metrics", sa.Column("maintainability_score", sa.Float, nullable=True)
    )
    op.add_column("health_file_metrics", sa.Column("performance_score", sa.Float, nullable=True))
    op.add_column("health_findings", sa.Column("dimension", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("health_findings", "dimension")
    op.drop_column("health_file_metrics", "performance_score")
    op.drop_column("health_file_metrics", "maintainability_score")
    op.drop_column("health_file_metrics", "defect_score")
