"""coverage_files: store the lines each report calls executable.

``coverable_lines_json`` holds the executable-line set, hit or not, so patch
coverage can be computed from stored coverage: without it an uncovered
changed line cannot be told from a changed comment. ``'[]'`` means the report
did not say (or the row predates the column).

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres.

Revision ID: 0079
Revises: 0078
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0079"
down_revision: str | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns("coverage_files")}


def upgrade() -> None:
    # Guarded: ``init_db``'s reconciler may already have added it on Postgres.
    if "coverable_lines_json" not in _columns():
        op.add_column(
            "coverage_files",
            sa.Column("coverable_lines_json", sa.Text(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    if "coverable_lines_json" in _columns():
        op.drop_column("coverage_files", "coverable_lines_json")
