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


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("function_facts")}


def upgrade() -> None:
    # A store the startup reconciler already reached holds the column.
    if "eager_defaults_json" not in _existing():
        op.add_column("function_facts", sa.Column("eager_defaults_json", sa.Text(), nullable=True))


def downgrade() -> None:
    if "eager_defaults_json" in _existing():
        with op.batch_alter_table("function_facts") as batch:
            batch.drop_column("eager_defaults_json")
