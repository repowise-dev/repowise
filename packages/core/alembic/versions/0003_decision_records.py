"""Add decision_records table for Architectural Decision Intelligence layer.

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Core content
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("context", sa.Text, nullable=False, server_default=""),
        sa.Column("decision", sa.Text, nullable=False, server_default=""),
        sa.Column("rationale", sa.Text, nullable=False, server_default=""),
        # JSON arrays
        sa.Column("alternatives_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("consequences_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("affected_files_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("affected_modules_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("tags_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("evidence_commits_json", sa.Text, nullable=False, server_default="[]"),
        # Provenance
        sa.Column("source", sa.String(32), nullable=False, server_default="cli"),
        sa.Column("evidence_file", sa.Text, nullable=True),
        sa.Column("evidence_line", sa.Integer, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        # Staleness
        sa.Column("last_code_change", sa.DateTime(timezone=True), nullable=True),
        sa.Column("staleness_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("superseded_by", sa.String(32), nullable=True),
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
        sa.UniqueConstraint(
            "repository_id",
            "title",
            "source",
            "evidence_file",
            name="uq_decision_record",
        ),
    )
    op.create_index(
        "ix_decision_records_repository_id",
        "decision_records",
        ["repository_id"],
    )
    op.create_index(
        "ix_decision_records_status",
        "decision_records",
        ["repository_id", "status"],
    )
    op.create_index(
        "ix_decision_records_source",
        "decision_records",
        ["repository_id", "source"],
    )


def downgrade() -> None:
    op.drop_table("decision_records")
