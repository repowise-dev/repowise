"""Record the health analyzer version on each health snapshot.

``health_snapshots.analyzer_version`` is the ``HEALTH_ANALYZER_VERSION`` that
wrote the row, so a trend can tell a scoring change from a code change.
Nullable: null means "not recorded".

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres.

Revision ID: 0117
Revises: 0116
Create Date: 2026-10-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0117"
down_revision: str | None = "0116"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns("health_snapshots")}


def upgrade() -> None:
    # Guarded: ``init_db``'s reconciler may already have added it on Postgres.
    if "analyzer_version" not in _columns():
        op.add_column(
            "health_snapshots", sa.Column("analyzer_version", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    if "analyzer_version" in _columns():
        op.drop_column("health_snapshots", "analyzer_version")
