"""Add community_meta_json column to graph_nodes.

Stores per-node community metadata (label, cohesion for file nodes;
symbol_community_id for symbol nodes) as a JSON text column.

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("graph_nodes") as batch_op:
        batch_op.add_column(
            sa.Column(
                "community_meta_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("graph_nodes") as batch_op:
        batch_op.drop_column("community_meta_json")
