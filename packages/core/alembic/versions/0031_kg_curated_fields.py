"""Carry curated knowledge-graph fields to the database.

The KG curation pass (kg_curation.py) exports layer ``subGroups``, enriched
tour steps (``target_path``/``layer_id``/``reason``/``depth``/``kind``/
``page_type``), ranked project entry points, and per-node curated metadata
(type/summary/tags) — all of which were dropped at the JSON → DB boundary.
This revision adds the missing columns plus two new tables so the
architecture view can serve curated data without re-reading workspace files.

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- Layers: curated sub-groups ([{id, name, nodeIds}]) -----------------
    op.add_column(
        "knowledge_graph_layers",
        sa.Column("sub_groups_json", sa.Text, nullable=False, server_default="[]"),
    )

    # -- Tour steps: curated, layer-aware fields ----------------------------
    op.add_column("knowledge_graph_tour_steps", sa.Column("target_path", sa.Text, nullable=True))
    op.add_column("knowledge_graph_tour_steps", sa.Column("layer_id", sa.Text, nullable=True))
    op.add_column(
        "knowledge_graph_tour_steps",
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
    )
    op.add_column("knowledge_graph_tour_steps", sa.Column("depth", sa.Integer, nullable=True))
    op.add_column(
        "knowledge_graph_tour_steps",
        sa.Column("kind", sa.Text, nullable=False, server_default=""),
    )
    op.add_column("knowledge_graph_tour_steps", sa.Column("page_type", sa.Text, nullable=True))

    # -- Project-level curated metadata (one row per repo) -------------------
    op.create_table(
        "kg_project_meta",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entry_points_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("entry_candidates_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("repository_id", name="uq_kg_project_meta"),
    )

    # -- Per-node curated metadata (presentation view: type/summary/tags) ----
    op.create_table(
        "kg_node_meta",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.Text, nullable=False),
        sa.Column("node_type", sa.Text, nullable=False, server_default="file"),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("tags_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("repository_id", "node_id", name="uq_kg_node_meta"),
    )
    op.create_index("ix_kg_node_meta_repository_id", "kg_node_meta", ["repository_id"])


def downgrade() -> None:
    op.drop_index("ix_kg_node_meta_repository_id", table_name="kg_node_meta")
    op.drop_table("kg_node_meta")
    op.drop_table("kg_project_meta")
    op.drop_column("knowledge_graph_tour_steps", "page_type")
    op.drop_column("knowledge_graph_tour_steps", "kind")
    op.drop_column("knowledge_graph_tour_steps", "depth")
    op.drop_column("knowledge_graph_tour_steps", "reason")
    op.drop_column("knowledge_graph_tour_steps", "layer_id")
    op.drop_column("knowledge_graph_tour_steps", "target_path")
    op.drop_column("knowledge_graph_layers", "sub_groups_json")
