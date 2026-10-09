"""Add mapping_partial to coverage_files.

A coverage ingest that maps fewer than half its report files to the repo tree
is a fragment, not repository coverage: the aggregate computed over the stored
subset (covered/total over the mapped rows only) is then presented as the
repo's number, which is the false-confidence trap of issue #1746. The flag is
a property of the whole delete-then-insert ingest, so it lives on the table
(any row of the latest batch reports it) rather than on individual rows.

Local SQLite stores get the column from the model via ``init_db``'s additive
schema reconciler; this migration covers the managed Postgres ones.

Revision ID: 0060
Revises: 0059
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "coverage_files",
        sa.Column("mapping_partial", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("coverage_files", "mapping_partial")
