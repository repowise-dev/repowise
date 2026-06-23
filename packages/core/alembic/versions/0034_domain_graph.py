"""Domain graph tables: behavior-oriented Domain / Flow / Step nodes + edges.

Adds the persistence for the synthesized domain graph (the artifact that
answers "what does the system do?"), mirroring the structural graph's
discriminated single-table-of-nodes + typed-edges shape:

- ``domain_graph_nodes`` holds Domain, Flow, and Step rows (``kind``), with the
  renderable wiki page content (``page_title`` / ``page_content``) stored
  alongside the structural fields on domain/flow rows.
- ``domain_graph_edges`` holds ``contains_flow`` / ``flow_step`` /
  ``cross_domain`` edges.

Additive and self-contained; no existing table is touched.

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-23
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
        "domain_graph_nodes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.Text, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.Text, nullable=False, server_default=""),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("parent_id", sa.Text, nullable=True),
        sa.Column("step_order", sa.Integer, nullable=True),
        sa.Column("implements_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("page_title", sa.Text, nullable=False, server_default=""),
        sa.Column("page_content", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_id", "node_id", name="uq_domain_graph_node"),
    )
    op.create_index(
        "ix_domain_graph_nodes_repo", "domain_graph_nodes", ["repository_id"]
    )

    op.create_table(
        "domain_graph_edges",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_node_id", sa.Text, nullable=False),
        sa.Column("target_node_id", sa.Text, nullable=False),
        sa.Column("edge_type", sa.String(32), nullable=False),
        sa.Column("weight", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "repository_id",
            "source_node_id",
            "target_node_id",
            "edge_type",
            name="uq_domain_graph_edge",
        ),
    )
    op.create_index(
        "ix_domain_graph_edges_repo", "domain_graph_edges", ["repository_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_domain_graph_edges_repo", table_name="domain_graph_edges")
    op.drop_table("domain_graph_edges")
    op.drop_index("ix_domain_graph_nodes_repo", table_name="domain_graph_nodes")
    op.drop_table("domain_graph_nodes")
