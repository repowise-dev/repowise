"""Materialize performance opportunities and give findings a queryable identity.

The performance queue was rebuilt from every open performance finding on every
request, so serving one page of twenty cost the whole repository. This adds the
table that holds the grouped result, the columns that make identity and plan
linkage indexable instead of JSON predicates, and the single-row current
summary a bare dashboard reads.

Every column is nullable or defaulted, and every index is additive, so an
existing store upgrades without a rewrite and repopulates on its next analysis.

Revision ID: 0057
Revises: 0056
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FINDING_INDEXES = (
    ("ix_health_findings_repo_public_id", ["repository_id", "public_id"]),
    (
        "ix_health_findings_repo_status_dimension_opportunity",
        ["repository_id", "status", "dimension", "opportunity_id"],
    ),
)

_OPPORTUNITY_INDEXES = (
    ("ix_performance_opportunities_repo_id", ["repository_id", "opportunity_id"]),
    (
        "ix_performance_opportunities_repo_status_rank",
        ["repository_id", "status", "rank_position"],
    ),
    (
        "ix_performance_opportunities_repo_status_context_rank",
        ["repository_id", "status", "execution_context", "rank_position"],
    ),
    (
        "ix_performance_opportunities_repo_status_action_rank",
        ["repository_id", "status", "actionability_state", "rank_position"],
    ),
    (
        "ix_performance_opportunities_repo_status_path",
        ["repository_id", "status", "file_path"],
    ),
)


def upgrade() -> None:
    op.add_column("health_findings", sa.Column("public_id", sa.String(64), nullable=True))
    op.add_column("health_findings", sa.Column("opportunity_id", sa.String(64), nullable=True))
    for name, columns in _FINDING_INDEXES:
        op.create_index(name, "health_findings", columns, if_not_exists=True)

    op.add_column(
        "refactoring_suggestions", sa.Column("opportunity_id", sa.String(64), nullable=True)
    )
    op.create_index(
        "ix_refactoring_suggestions_repo_type_opportunity",
        "refactoring_suggestions",
        ["repository_id", "refactoring_type", "opportunity_id"],
        if_not_exists=True,
    )

    op.create_table(
        "performance_opportunities",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("opportunity_id", sa.String(64), nullable=False),
        sa.Column(
            "performance_model_version", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("rank_position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rank_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "execution_context", sa.String(16), nullable=False, server_default="unknown"
        ),
        sa.Column("boundary_kind", sa.String(32), nullable=True),
        sa.Column("biomarker_type", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "actionability_state", sa.String(16), nullable=False, server_default="investigate"
        ),
        sa.Column("evidence_confidence", sa.String(16), nullable=False, server_default="low"),
        sa.Column("plan_state", sa.String(16), nullable=False, server_default="no_safe_plan"),
        sa.Column("fix_strategy", sa.String(64), nullable=True),
        sa.Column("fix_safety", sa.String(16), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("intervention_symbol", sa.Text(), nullable=True),
        sa.Column("terminal_sink", sa.Text(), nullable=True),
        sa.Column("observations_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "affected_call_sites_total", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("affected_files_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("analyzed_commit", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "repository_id",
            "performance_model_version",
            "opportunity_id",
            name="uq_performance_opportunities_repo_model_id",
        ),
    )
    for name, columns in _OPPORTUNITY_INDEXES:
        op.create_index(name, "performance_opportunities", columns, if_not_exists=True)

    op.create_table(
        "performance_summaries",
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "performance_model_version", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("opportunities_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("analyzed_commit", sa.String(40), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("performance_summaries")
    for name, _columns in _OPPORTUNITY_INDEXES:
        op.drop_index(name, table_name="performance_opportunities", if_exists=True)
    op.drop_table("performance_opportunities")

    op.drop_index(
        "ix_refactoring_suggestions_repo_type_opportunity",
        table_name="refactoring_suggestions",
        if_exists=True,
    )
    op.drop_column("refactoring_suggestions", "opportunity_id")

    for name, _columns in _FINDING_INDEXES:
        op.drop_index(name, table_name="health_findings", if_exists=True)
    op.drop_column("health_findings", "opportunity_id")
    op.drop_column("health_findings", "public_id")
