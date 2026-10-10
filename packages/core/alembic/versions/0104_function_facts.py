"""Add ``function_facts``: one row per function symbol, its execution role and body facts.

Local SQLite stores get the table from ``init_db``; this migration covers
managed Postgres. Rows appear on the next analysis.

Revision ID: 0103
Revises: 0102
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0103"
down_revision: str | None = "0102"
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
        sa.Column("symbol_id", sa.Text(), primary_key=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("execution_role", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("awaits", sa.Boolean(), nullable=True),
        sa.Column("is_generator", sa.Boolean(), nullable=True),
        sa.Column("uses_receiver", sa.Boolean(), nullable=True),
        sa.Column("receiver_assigns_json", sa.Text(), nullable=True),
        sa.Column("early_exits", sa.Integer(), nullable=True),
    )
    op.create_index("ix_function_facts_repo_path", "function_facts", ["repository_id", "file_path"])


def downgrade() -> None:
    op.drop_index("ix_function_facts_repo_path", table_name="function_facts")
    op.drop_table("function_facts")
