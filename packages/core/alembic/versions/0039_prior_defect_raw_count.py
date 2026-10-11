"""Unfiltered prior-defect count alongside the shape-filtered one.

``prior_defect_count`` now counts only bug-fix commits whose diff actually
changes production code (doc-only, test-only, config-only and comment-only
fixes are dropped — see ``ingestion.git_indexer.fix_shape``). The pre-filter
total moves into ``prior_defect_raw_count`` so the noise a repo carries stays
visible and the two can be compared per file.

Defaulted and back-populated on the next index, like every other git_metadata
column: existing indexes read 0 until then.

Revision ID: 0039
Revises: 0038
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_metadata",
        sa.Column("prior_defect_raw_count", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "prior_defect_raw_count")
