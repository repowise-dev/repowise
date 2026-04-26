"""Add answer_cache table for get_answer LLM synthesis caching.

Caches the full JSON payload of a get_answer response keyed by repository
and question hash. Repeat questions from the agent return zero-LLM-cost
hits.

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_cache",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_hash", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("model_name", sa.String(128), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "repository_id", "question_hash", name="uq_answer_cache_q"
        ),
    )
    op.create_index(
        "ix_answer_cache_repo",
        "answer_cache",
        ["repository_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_answer_cache_repo", table_name="answer_cache")
    op.drop_table("answer_cache")
