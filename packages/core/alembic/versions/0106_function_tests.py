"""Add ``function_facts.tests_json``: each function's nearest test functions.

Local SQLite stores get the column from ``init_db``; this migration covers
managed Postgres. Values appear on the next analysis.

Revision ID: 0106
Revises: 0105
Create Date: 2026-10-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0106"
down_revision: str | None = "0105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("function_facts", sa.Column("tests_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("function_facts", "tests_json")
