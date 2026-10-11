"""Add llm_costs table for runtime LLM cost tracking.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_costs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("operation", sa.String(50), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cost_usd", sa.Float, nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=True),
    )
    op.create_index(
        "ix_llm_costs_repository_ts",
        "llm_costs",
        ["repository_id", "ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_costs_repository_ts", table_name="llm_costs")
    op.drop_table("llm_costs")
