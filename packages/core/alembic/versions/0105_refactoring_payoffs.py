"""Add per-function size measures to ``function_facts`` and the ``refactoring_payoffs`` table.

``function_facts`` gains CCN, code lines, parameter count and deepest nesting
from the complexity walk. ``refactoring_payoffs`` records, for a plan the writer
resolved as no longer detected, whether it was applied, its file deleted or
its target changed, with the target's measures before and after.

Local SQLite stores get both from ``init_db``; this migration covers managed
Postgres. Values appear on the next analysis.

Revision ID: 0105
Revises: 0104
Create Date: 2026-10-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0105"
down_revision: str | None = "0104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FACT_COLUMNS = ("ccn", "nloc", "params", "max_nesting")


def upgrade() -> None:
    for name in _FACT_COLUMNS:
        op.add_column("function_facts", sa.Column(name, sa.Integer(), nullable=True))
    op.create_table(
        "refactoring_payoffs",
        sa.Column(
            "suggestion_id",
            sa.String(32),
            sa.ForeignKey("refactoring_suggestions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("resolved_commit", sa.String(64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("before_ccn", sa.Integer(), nullable=True),
        sa.Column("before_nloc", sa.Integer(), nullable=True),
        sa.Column("before_params", sa.Integer(), nullable=True),
        sa.Column("after_ccn", sa.Integer(), nullable=True),
        sa.Column("after_nloc", sa.Integer(), nullable=True),
        sa.Column("after_params", sa.Integer(), nullable=True),
        sa.Column("new_symbol", sa.Text(), nullable=True),
        sa.Column("stage", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_refactoring_payoffs_repo_outcome",
        "refactoring_payoffs",
        ["repository_id", "outcome"],
    )


def downgrade() -> None:
    op.drop_index("ix_refactoring_payoffs_repo_outcome", table_name="refactoring_payoffs")
    op.drop_table("refactoring_payoffs")
    for name in reversed(_FACT_COLUMNS):
        op.drop_column("function_facts", name)
