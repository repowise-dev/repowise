"""Add ``function_facts``: one row per function symbol, its execution role and body facts.

Local SQLite stores get the table from ``init_db``; this migration covers
managed Postgres. Rows appear on the next analysis.

Revision ID: 0104
Revises: 0103
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0104"
down_revision: str | None = "0103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "function_facts",
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # Bytewise order, so a file's rows are one key range of the id.
        sa.Column(
            "symbol_id",
            sa.Text().with_variant(sa.Text(collation="C"), "postgresql"),
            primary_key=True,
        ),
        sa.Column("execution_role", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("awaits", sa.Boolean(), nullable=True),
        sa.Column("is_generator", sa.Boolean(), nullable=True),
        sa.Column("uses_receiver", sa.Boolean(), nullable=True),
        sa.Column("receiver_assigns_known", sa.Boolean(), nullable=True),
        sa.Column("receiver_assigns_json", sa.Text(), nullable=True),
        sa.Column("early_exits", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("function_facts")
