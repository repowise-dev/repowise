"""health_file_metrics: record where each scored file's code comes from.

``code_origin`` is one of production, test, vendored, docs_example, generated
or tooling, decided at score time with the file's head in hand. Nullable with
no backfill: the analyzer stamp that ships with it re-scores every file.

Local SQLite stores get the column from ``init_db``'s additive reconciler; this
migration covers managed Postgres.

Revision ID: 0092
Revises: 0091
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0092"
down_revision: str | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "health_file_metrics",
        sa.Column("code_origin", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("health_file_metrics", "code_origin")
