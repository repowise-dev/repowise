"""Add ``graph_nodes.public_api``: the file a package exports a symbol from.

Local SQLite stores get the column from ``init_db``; this migration covers
managed Postgres. Values appear on the next index.

Revision ID: 0107
Revises: 0106
Create Date: 2026-10-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0107"
down_revision: str | None = "0106"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("graph_nodes", sa.Column("public_api", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("graph_nodes", "public_api")
