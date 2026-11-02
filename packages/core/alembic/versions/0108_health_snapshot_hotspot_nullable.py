"""Allow ``health_snapshots.hotspot_health`` to be NULL (PostgreSQL only).

A repository with no scored hotspot file has no hotspot health, so its
snapshot now stores NULL instead of a 10.0 that reads as perfect hotspots.

Local SQLite stores never run Alembic; ``init_db`` rebuilds that table there
when the column is still NOT NULL, and new stores get it from the model.

Revision ID: 0108
Revises: 0107
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0108"
down_revision: str | None = "0107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column("health_snapshots", "hotspot_health", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    """Restore NOT NULL with the value the old schema wrote for such snapshots."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("UPDATE health_snapshots SET hotspot_health = 10.0 WHERE hotspot_health IS NULL")
    op.alter_column("health_snapshots", "hotspot_health", existing_type=sa.Float(), nullable=False)
