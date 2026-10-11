"""refactoring_suggestions table — structured Extract Class (and later) plans.

The refactoring layer turns the structural signals the health pass already
computes (LCOM4 cohesion components in Phase 1) into structured
``RefactoringSuggestion`` rows: the concrete plan, the evidence behind it,
and the blast radius of applying it. The JSON payload columns
(``plan_json`` / ``evidence_json`` / ``blast_radius_json``) keep the schema
type-agnostic so later refactoring types add rows, not columns.

Mirrors the ``health_findings`` write model: delete-then-insert per repo on
a full index, upsert-by-file on an incremental update.

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refactoring_suggestions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("refactoring_type", sa.String(32), nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("target_symbol", sa.String(255), nullable=False, server_default=""),
        sa.Column("line_start", sa.Integer, nullable=True),
        sa.Column("line_end", sa.Integer, nullable=True),
        sa.Column("plan_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("evidence_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("impact_delta", sa.Float, nullable=False, server_default="0"),
        sa.Column("effort_bucket", sa.String(8), nullable=False, server_default=""),
        sa.Column("blast_radius_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("source_biomarker", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_refactoring_suggestions_repo",
        "refactoring_suggestions",
        ["repository_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_refactoring_suggestions_repo", "refactoring_suggestions")
    op.drop_table("refactoring_suggestions")
