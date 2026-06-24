"""graph_node_membership table — queryable SCC + symbol-community rows.

The break-cycle and move-method refactoring detectors reason over two
structural facts the graph already carries but never persisted as rows:
file-level strongly-connected components (import cycles) and symbol-level
communities. They were only reachable by rebuilding the in-memory graph (or
buried inside ``graph_nodes.community_meta_json``). This table materializes
both as queryable rows — ``scc_id`` / ``scc_size`` for file nodes that sit in
a real (size >= 2) cycle, and ``symbol_community_id`` for symbol nodes — so
the web layer can read cycles and communities without re-running NetworkX.

Additive to ``graph_nodes`` / ``graph_metrics``; written on full index and
refreshed on the incremental graph-node path. Non-load-bearing: a failure to
materialize never fails the index.

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_node_membership",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.Text, nullable=False),
        sa.Column("node_type", sa.String(16), nullable=False, server_default="file"),
        sa.Column("scc_id", sa.Integer, nullable=True),
        sa.Column("scc_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("symbol_community_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_id", "node_id", name="uq_graph_node_membership"),
    )
    op.create_index(
        "ix_graph_node_membership_repo",
        "graph_node_membership",
        ["repository_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_graph_node_membership_repo", "graph_node_membership")
    op.drop_table("graph_node_membership")
