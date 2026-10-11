"""Add edge_type column to graph_edges table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "graph_edges",
        sa.Column("edge_type", sa.String(64), nullable=True, server_default="imports"),
    )


def downgrade() -> None:
    op.drop_column("graph_edges", "edge_type")
