"""Add decision_evidence table + decision_records.verification column.

Phase 1 of the decision-layer overhaul: provenance accretes instead of
overwriting. One decision can now be backed by many evidence rows (one per
source that attested to it); the headline decision keeps the highest-rank
source's fields, and ``verification`` records whether those fields are a
verbatim quote of their source span (anti-hallucination substring gate).

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_records",
        sa.Column(
            "verification",
            sa.String(16),
            nullable=False,
            server_default="unverified",
        ),
    )

    op.create_table(
        "decision_evidence",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "decision_id",
            sa.String(32),
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_rank", sa.Integer, nullable=False, server_default="1"),
        sa.Column("evidence_file", sa.Text, nullable=True),
        sa.Column("evidence_line", sa.Integer, nullable=True),
        sa.Column("evidence_commit", sa.String(64), nullable=True),
        sa.Column("source_quote", sa.Text, nullable=False, server_default=""),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0.0"),
        sa.Column(
            "verification",
            sa.String(16),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "decision_id",
            "source",
            "evidence_file",
            "evidence_commit",
            name="uq_decision_evidence",
        ),
    )
    op.create_index(
        "ix_decision_evidence_decision_id",
        "decision_evidence",
        ["decision_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_decision_evidence_decision_id", table_name="decision_evidence")
    op.drop_table("decision_evidence")
    op.drop_column("decision_records", "verification")
