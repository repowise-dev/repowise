"""Add composite indexes for performant graph queries.

Indexes support the new MCP graph query tools (get_callers_callees,
get_community, get_graph_metrics, get_execution_flows) which filter
by (repo, node_type, community) and (repo, source/target, edge_type).

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-12
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_graph_nodes_repo_type_community",
        "graph_nodes",
        ["repository_id", "node_type", "community_id"],
    )
    op.create_index(
        "ix_graph_edges_repo_target_type",
        "graph_edges",
        ["repository_id", "target_node_id", "edge_type"],
    )
    op.create_index(
        "ix_graph_edges_repo_source_type",
        "graph_edges",
        ["repository_id", "source_node_id", "edge_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_graph_edges_repo_source_type", table_name="graph_edges")
    op.drop_index("ix_graph_edges_repo_target_type", table_name="graph_edges")
    op.drop_index("ix_graph_nodes_repo_type_community", table_name="graph_nodes")
