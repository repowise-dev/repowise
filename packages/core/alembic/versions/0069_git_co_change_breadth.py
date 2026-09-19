"""git_metadata: measure co-change breadth instead of counting a capped list.

``co_change_partners_json`` keeps only the strongest ``MAX_PARTNERS_PER_FILE``
partners, so counting it saturates. These three are measured over every partner
before that truncation, with ``co_change_scatter_pct`` ranking the mass
repo-relative as ``change_entropy_pct`` does. They default to 0 and cannot be
backfilled -- the counts above the cap were discarded at write time -- so
``co_change_scatter`` stays silent until the next walk repopulates them.

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres.

Revision ID: 0069
Revises: 0068
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_metadata",
        sa.Column("co_change_partner_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "git_metadata",
        sa.Column("co_change_mass", sa.Float, nullable=False, server_default="0.0"),
    )
    op.add_column(
        "git_metadata",
        sa.Column("co_change_scatter_pct", sa.Float, nullable=False, server_default="0.0"),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "co_change_scatter_pct")
    op.drop_column("git_metadata", "co_change_mass")
    op.drop_column("git_metadata", "co_change_partner_count")
