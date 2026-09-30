"""decision_records: mark a record whose named files are all gone at HEAD.

Local SQLite stores never run Alembic (``init_db``'s reconciler adds missing
columns), so this migration exists for managed Postgres. Existing rows have
not been checked yet, hence ``false``; the next index fills it.

Revision ID: 0087
Revises: 0086
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0087"
down_revision: str | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_records",
        sa.Column("artifacts_gone", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("decision_records", "artifacts_gone")
