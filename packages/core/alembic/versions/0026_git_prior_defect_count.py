"""Add prior_defect_count column to git_metadata.

Stores the count of bug-fix commits touching a file in the trailing ~6-month
defect window (anchored to the index's as_of reference so historical/T0 scoring
stays leakage-free). Computed during per-file git history indexing and consumed
by the ``prior_defect`` health biomarker.

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_metadata",
        sa.Column("prior_defect_count", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "prior_defect_count")
