"""Add symbol-level graph nodes and call edges with confidence scoring.

Extends graph_nodes with symbol-specific columns (kind, name, file_path,
start_line, end_line, visibility, signature, parent_symbol_id) and adds
confidence to graph_edges. Updates the unique constraint on graph_edges
to include edge_type for multi-edge support.

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- graph_nodes: add symbol-level columns ---
    with op.batch_alter_table("graph_nodes") as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("name", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("qualified_name", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("file_path", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("start_line", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("end_line", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("visibility", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("signature", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("parent_symbol_id", sa.Text(), nullable=True))

    # --- graph_edges: add confidence column ---
    with op.batch_alter_table("graph_edges") as batch_op:
        batch_op.add_column(
            sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0")
        )
        # Make edge_type non-nullable with a default
        batch_op.alter_column(
            "edge_type",
            existing_type=sa.String(64),
            nullable=False,
            server_default="imports",
        )
        # Drop old unique constraint, add new one that includes edge_type
        batch_op.drop_constraint("uq_graph_edge", type_="unique")
        batch_op.create_unique_constraint(
            "uq_graph_edge_typed",
            ["repository_id", "source_node_id", "target_node_id", "edge_type"],
        )


def downgrade() -> None:
    with op.batch_alter_table("graph_edges") as batch_op:
        batch_op.drop_constraint("uq_graph_edge_typed", type_="unique")
        batch_op.create_unique_constraint(
            "uq_graph_edge",
            ["repository_id", "source_node_id", "target_node_id"],
        )
        batch_op.alter_column(
            "edge_type",
            existing_type=sa.String(64),
            nullable=True,
            server_default=None,
        )
        batch_op.drop_column("confidence")

    with op.batch_alter_table("graph_nodes") as batch_op:
        batch_op.drop_column("parent_symbol_id")
        batch_op.drop_column("signature")
        batch_op.drop_column("visibility")
        batch_op.drop_column("end_line")
        batch_op.drop_column("start_line")
        batch_op.drop_column("file_path")
        batch_op.drop_column("qualified_name")
        batch_op.drop_column("name")
        batch_op.drop_column("kind")
