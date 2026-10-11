"""Add graph_metrics table — materialized file-level graph metrics snapshot.

Lets large-repo metric reads (PageRank / betweenness / community / in-out
degree) be served from SQL instead of recomputing the NetworkX kernels.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_metrics",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.Text, nullable=False),
        sa.Column("pagerank", sa.Float, nullable=False, server_default="0"),
        sa.Column("betweenness", sa.Float, nullable=False, server_default="0"),
        sa.Column("community_id", sa.Integer, nullable=False, server_default="0"),
        sa.Column("in_degree", sa.Integer, nullable=False, server_default="0"),
        sa.Column("out_degree", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("repository_id", "node_id", name="uq_graph_metric"),
    )
    op.create_index(
        "ix_graph_metrics_repository_id",
        "graph_metrics",
        ["repository_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_graph_metrics_repository_id", table_name="graph_metrics")
    op.drop_table("graph_metrics")
