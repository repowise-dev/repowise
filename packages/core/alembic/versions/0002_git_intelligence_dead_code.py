"""Add git_metadata and dead_code_findings tables for Phase 5.5.

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # git_metadata
    # ------------------------------------------------------------------
    op.create_table(
        "git_metadata",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text, nullable=False),
        # Commit volume
        sa.Column("commit_count_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("commit_count_90d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("commit_count_30d", sa.Integer, nullable=False, server_default="0"),
        # Timeline
        sa.Column("first_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_commit_at", sa.DateTime(timezone=True), nullable=True),
        # Ownership
        sa.Column("primary_owner_name", sa.String(255), nullable=True),
        sa.Column("primary_owner_email", sa.String(255), nullable=True),
        sa.Column("primary_owner_commit_pct", sa.Float, nullable=True),
        # JSON fields
        sa.Column("top_authors_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("significant_commits_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("co_change_partners_json", sa.Text, nullable=False, server_default="[]"),
        # Derived signals
        sa.Column("is_hotspot", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("is_stable", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("churn_percentile", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("age_days", sa.Integer, nullable=False, server_default="0"),
        # Timestamps
        sa.Column(
            "created_at",
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
        sa.UniqueConstraint("repository_id", "file_path", name="uq_git_metadata"),
    )
    op.create_index("ix_git_metadata_repository_id", "git_metadata", ["repository_id"])
    op.create_index(
        "ix_git_metadata_repo_file",
        "git_metadata",
        ["repository_id", "file_path"],
    )

    # ------------------------------------------------------------------
    # dead_code_findings
    # ------------------------------------------------------------------
    op.create_table(
        "dead_code_findings",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("symbol_name", sa.String(255), nullable=True),
        sa.Column("symbol_kind", sa.String(32), nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("last_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("commit_count_90d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lines", sa.Integer, nullable=False, server_default="0"),
        sa.Column("package", sa.String(255), nullable=True),
        sa.Column("evidence_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("safe_to_delete", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("primary_owner", sa.String(255), nullable=True),
        sa.Column("age_days", sa.Integer, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_dead_code_findings_repository_id",
        "dead_code_findings",
        ["repository_id"],
    )


def downgrade() -> None:
    op.drop_table("dead_code_findings")
    op.drop_table("git_metadata")
