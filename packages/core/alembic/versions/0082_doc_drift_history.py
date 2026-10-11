"""Documentation drift history: when a finding first appeared, and the count per snapshot.

``doc_drift_findings.first_seen_at`` is carried across rewrites by the
finding's line-independent key, so a finding can be shown as new since the
last update. ``health_snapshots.doc_drift_count`` records the stored finding
count beside each snapshot's health figures. Both are nullable: null means
"not recorded", never zero.

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres.

Revision ID: 0082
Revises: 0081
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("doc_drift_findings", "first_seen_at", sa.DateTime(timezone=True)),
    ("health_snapshots", "doc_drift_count", sa.Integer()),
)


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    # Guarded: ``init_db``'s reconciler may already have added these on Postgres.
    for table, name, type_ in _COLUMNS:
        if name not in _columns(table):
            op.add_column(table, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for table, name, _type in reversed(_COLUMNS):
        if name in _columns(table):
            op.drop_column(table, name)
