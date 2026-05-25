"""Add knowledge graph layer and tour step tables.

Stores KG layers (functional subsystems detected via community detection
+ LLM enrichment) and guided tour steps for codebase onboarding.

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_graph_layers",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("layer_id", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("node_ids_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("repository_id", "layer_id", name="uq_kg_layer"),
    )
    op.create_index(
        "ix_kg_layers_repository_id",
        "knowledge_graph_layers",
        ["repository_id"],
    )

    op.create_table(
        "knowledge_graph_tour_steps",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_order", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("node_ids_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("repository_id", "step_order", name="uq_kg_tour_step"),
    )
    op.create_index(
        "ix_kg_tour_steps_repository_id",
        "knowledge_graph_tour_steps",
        ["repository_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_kg_tour_steps_repository_id", table_name="knowledge_graph_tour_steps")
    op.drop_table("knowledge_graph_tour_steps")
    op.drop_index("ix_kg_layers_repository_id", table_name="knowledge_graph_layers")
    op.drop_table("knowledge_graph_layers")
