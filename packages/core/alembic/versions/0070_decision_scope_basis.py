"""decision_records: record how a decision's file list was arrived at.

``affected_files_json`` was doing two jobs. For a record mined from a commit
it is the provenance of the change, which is worth keeping. It was also the
governance binding every path-scoped surface reads, and there it is mostly
false: the commit-derived miners take the commit's entire file list, so a
decision mined from a large refactor claimed every file the refactor touched.

This column separates the two. ``commit_footprint`` keeps the files and stops
the record answering for each one, ``stated`` marks a scope a person wrote
down, and the empty default binds as before.

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres. The data repair is deliberately not here: it lives in
``decision_migration.backfill_scope_basis`` and runs on every index, because a
fix in a migration and a fix in the code eventually disagree, and only one of
them runs on an existing store.

Revision ID: 0070
Revises: 0069
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_records",
        sa.Column(
            "scope_basis",
            sa.String(length=32),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("decision_records", "scope_basis")
