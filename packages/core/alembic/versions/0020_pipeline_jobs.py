"""Add pipeline_jobs table for checkpoint/resume state.

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipeline_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("cursor", sa.Text, nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_pipeline_jobs_repository_id",
        "pipeline_jobs",
        ["repository_id"],
    )
    op.create_index(
        "ix_pipeline_jobs_repo_state",
        "pipeline_jobs",
        ["repository_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_jobs_repo_state", table_name="pipeline_jobs")
    op.drop_index("ix_pipeline_jobs_repository_id", table_name="pipeline_jobs")
    op.drop_table("pipeline_jobs")
