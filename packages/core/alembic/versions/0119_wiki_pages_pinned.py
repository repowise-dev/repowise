"""Add ``wiki_pages.pinned``: a user hand-requested this page's doc.

A pinned page always enters generation selection, so a doc a user asked for
keeps following the code (issue #812). Not null, default false.

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres.

Revision ID: 0119
Revises: 0118
Create Date: 2026-10-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0119"
down_revision: str | None = "0118"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns("wiki_pages")}


def upgrade() -> None:
    # Guarded: ``init_db``'s reconciler may already have added it on Postgres.
    if "pinned" not in _columns():
        op.add_column(
            "wiki_pages",
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    if "pinned" in _columns():
        op.drop_column("wiki_pages", "pinned")
