"""Add ``git_function_blame.history_only``: a row kept for owner and last change.

Functions in a file under the blame commit floor, or in a production file over
the biomarkers' size cap, now get a row too, so a measured function's owner
and last change come from its own lines. Health readers skip these rows. Not
null, default false.

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres.

Revision ID: 0145
Revises: 0143
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0145"
down_revision: str | None = "0143"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns("git_function_blame")}


def upgrade() -> None:
    # Guarded: ``init_db``'s reconciler may already have added it on Postgres.
    if "history_only" not in _columns():
        op.add_column(
            "git_function_blame",
            sa.Column("history_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    if "history_only" in _columns():
        with op.batch_alter_table("git_function_blame") as batch:
            batch.drop_column("history_only")
