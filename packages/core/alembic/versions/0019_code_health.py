"""Add code-health tables: findings, file metrics, snapshots, coverage.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # health_findings — one row per biomarker hit
    # ------------------------------------------------------------------
    op.create_table(
        "health_findings",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("biomarker_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("function_name", sa.String(255), nullable=True),
        sa.Column("line_start", sa.Integer, nullable=True),
        sa.Column("line_end", sa.Integer, nullable=True),
        sa.Column("details_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("health_impact", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
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
    )
    op.create_index(
        "ix_health_findings_repository_id",
        "health_findings",
        ["repository_id"],
    )
    op.create_index(
        "ix_health_findings_repo_file",
        "health_findings",
        ["repository_id", "file_path"],
    )

    # ------------------------------------------------------------------
    # health_file_metrics — one row per file (unique on repo + path)
    # ------------------------------------------------------------------
    op.create_table(
        "health_file_metrics",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("score", sa.Float, nullable=False, server_default="10.0"),
        sa.Column("max_ccn", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_nesting", sa.Integer, nullable=False, server_default="0"),
        sa.Column("nloc", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duplication_pct", sa.Float, nullable=True),
        sa.Column("has_test_file", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("line_coverage_pct", sa.Float, nullable=True),
        sa.Column("branch_coverage_pct", sa.Float, nullable=True),
        sa.Column("module", sa.String(255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("repository_id", "file_path", name="uq_health_file_metrics"),
    )
    op.create_index(
        "ix_health_file_metrics_repo",
        "health_file_metrics",
        ["repository_id"],
    )

    # ------------------------------------------------------------------
    # health_snapshots — KPI history + compact per-file score map
    # ------------------------------------------------------------------
    op.create_table(
        "health_snapshots",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "taken_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("hotspot_health", sa.Float, nullable=False, server_default="10.0"),
        sa.Column("average_health", sa.Float, nullable=False, server_default="10.0"),
        sa.Column("worst_performer_path", sa.Text, nullable=True),
        sa.Column("worst_performer_score", sa.Float, nullable=True),
        sa.Column("per_file_scores_json", sa.Text, nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_health_snapshots_repo",
        "health_snapshots",
        ["repository_id"],
    )

    # ------------------------------------------------------------------
    # coverage_files — last-ingestion coverage per file
    # ------------------------------------------------------------------
    op.create_table(
        "coverage_files",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("source_format", sa.String(32), nullable=False),
        sa.Column("line_coverage_pct", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("branch_coverage_pct", sa.Float, nullable=True),
        sa.Column("covered_lines_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("total_coverable_lines", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ingested_commit_sha", sa.String(40), nullable=True),
        sa.UniqueConstraint("repository_id", "file_path", name="uq_coverage_files"),
    )
    op.create_index(
        "ix_coverage_files_repo",
        "coverage_files",
        ["repository_id"],
    )


def downgrade() -> None:
    op.drop_table("coverage_files")
    op.drop_table("health_snapshots")
    op.drop_table("health_file_metrics")
    op.drop_table("health_findings")
