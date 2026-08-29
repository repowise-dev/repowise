"""Materialize composed refactoring opportunities and the repository headline.

Composition folds a file's plans into one ordered opportunity, and the
validation profile behind each step costs a test-reachability walk. Both ran
per request over every open plan, so a page of twenty cost the repository:
measured on the dogfood index, folding 2,283 plans is 91 ms (787 ms at ten
times the rows) and hydrating their validation is 1,118 ms.

This adds the read model the serving layer pages: one row per opportunity with
the filter, order and identity columns a query needs, and one summary row per
repository for the bare-dashboard directive. Both are derived state, rebuilt by
the finalizer on every analysis, so an existing store upgrades empty and
repopulates on its next run.

Revision ID: 0059
Revises: 0058
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPPORTUNITY_INDEXES = (
    ("ix_refactoring_opportunities_repo_id", ["repository_id", "opportunity_id"]),
    (
        "ix_refactoring_opportunities_repo_status_queue",
        ["repository_id", "status", "queue_position"],
    ),
    (
        "ix_refactoring_opportunities_repo_status_rank",
        ["repository_id", "status", "rank_position"],
    ),
    (
        "ix_refactoring_opportunities_repo_status_type_rank",
        ["repository_id", "status", "lead_refactoring_type", "rank_position"],
    ),
    (
        "ix_refactoring_opportunities_repo_status_path",
        ["repository_id", "status", "file_path"],
    ),
)


def upgrade() -> None:
    op.create_table(
        "refactoring_opportunities",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("opportunity_id", sa.String(64), nullable=False),
        sa.Column(
            "refactoring_model_version", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("rank_position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queue_position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rank_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("file_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("lead_biomarker", sa.String(64), nullable=True),
        sa.Column("lead_refactoring_type", sa.String(32), nullable=False, server_default=""),
        # Nullable on purpose: "no lead to compare against" is a different
        # answer from "does not address the lead", and the surface says so.
        sa.Column("addresses_primary_problem", sa.Boolean(), nullable=True),
        sa.Column("effort_bucket", sa.String(4), nullable=False, server_default="M"),
        sa.Column("confidence", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mechanical_steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("judgment_steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("affected_files_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recoverable_health", sa.Float(), nullable=False, server_default="0"),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("analyzed_commit", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "repository_id",
            "refactoring_model_version",
            "opportunity_id",
            name="uq_refactoring_opportunities_repo_model_id",
        ),
    )
    for name, columns in _OPPORTUNITY_INDEXES:
        op.create_index(name, "refactoring_opportunities", columns, if_not_exists=True)

    op.create_table(
        "refactoring_summaries",
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "refactoring_model_version", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("opportunities_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("analyzed_commit", sa.String(40), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("refactoring_summaries")
    for name, _columns in _OPPORTUNITY_INDEXES:
        op.drop_index(name, table_name="refactoring_opportunities", if_exists=True)
    op.drop_table("refactoring_opportunities")
