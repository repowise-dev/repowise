"""graph_nodes: split the dead-code exemption from the entry-point flag.

``is_reachability_root`` marks a file reached from outside the import graph (a
runner or loader starts it), which dead-code analysis never flags.
``is_entry_point`` keeps meaning "where a reader enters the system". Existing
rows back-fill from ``is_entry_point``, the flag that carried both jobs.

Local SQLite stores get the column from ``init_db``'s additive reconciler; this
migration covers managed Postgres.

Revision ID: 0086
Revises: 0085
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "graph_nodes",
        sa.Column("is_reachability_root", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE graph_nodes SET is_reachability_root = is_entry_point")


def downgrade() -> None:
    op.drop_column("graph_nodes", "is_reachability_root")
