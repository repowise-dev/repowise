"""doc_drift_findings: store the likely replacement for a missing target.

``suggestion`` is the path, anchor or target the finding's reference probably
means now, and ``suggestion_basis`` names the evidence (a package split, a git
rename, a similar heading or target). Both are null when nothing was found; a
suggestion never changes a finding's verdict or confidence.

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres.

Revision ID: 0078
Revises: 0077
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns("doc_drift_findings")}


def upgrade() -> None:
    # Guarded: ``init_db``'s reconciler may already have added these on Postgres.
    existing = _columns()
    if "suggestion" not in existing:
        op.add_column(
            "doc_drift_findings",
            sa.Column("suggestion", sa.String(length=1024), nullable=True),
        )
    if "suggestion_basis" not in existing:
        op.add_column(
            "doc_drift_findings",
            sa.Column("suggestion_basis", sa.String(length=32), nullable=True),
        )


def downgrade() -> None:
    existing = _columns()
    if "suggestion_basis" in existing:
        op.drop_column("doc_drift_findings", "suggestion_basis")
    if "suggestion" in existing:
        op.drop_column("doc_drift_findings", "suggestion")
