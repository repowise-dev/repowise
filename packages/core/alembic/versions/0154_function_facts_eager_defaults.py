"""Add ``function_facts.eager_defaults_json``: values built as an eager ``setdefault`` default.

Local SQLite stores get the column from ``init_db``; this migration covers
managed Postgres. Values appear on the next analysis of each file.

Revision ID: 0154
Revises: 0153
Create Date: 2026-11-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0154"
down_revision: str | None = "0153"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("function_facts", sa.Column("eager_defaults_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("function_facts", "eager_defaults_json")
