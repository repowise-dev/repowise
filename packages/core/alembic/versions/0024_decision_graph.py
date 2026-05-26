"""Add decision_edges + decision_node_links (the decision graph).

Phase 3 of the decision-layer overhaul: stop treating decisions as a flat
list. ``decision_edges`` makes decision→decision relationships first-class and
typed (``supersedes`` / ``refines`` / ``relates_to`` / ``conflicts_with``) so
supersession lineage is traversable. ``decision_node_links`` promotes the
decision→code linkage out of the denormalized ``affected_files_json`` /
``affected_modules_json`` arrays into rows that can be queried in *both*
directions — file → governing decisions and decision → governed entities. The
JSON arrays are kept as a read cache; these rows are the source of truth for
graph traversal.

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_edges",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "src_decision_id",
            sa.String(32),
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dst_decision_id",
            sa.String(32),
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # supersedes | refines | relates_to | conflicts_with
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("evidence", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "src_decision_id",
            "dst_decision_id",
            "kind",
            name="uq_decision_edge",
        ),
    )
    op.create_index("ix_decision_edges_repo", "decision_edges", ["repository_id"])
    op.create_index("ix_decision_edges_src", "decision_edges", ["src_decision_id"])
    op.create_index("ix_decision_edges_dst", "decision_edges", ["dst_decision_id"])

    op.create_table(
        "decision_node_links",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "decision_id",
            sa.String(32),
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.Text, nullable=False),
        # file | module
        sa.Column("link_type", sa.String(16), nullable=False, server_default="file"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "decision_id",
            "node_id",
            "link_type",
            name="uq_decision_node_link",
        ),
    )
    op.create_index("ix_decision_node_links_repo", "decision_node_links", ["repository_id"])
    op.create_index("ix_decision_node_links_decision", "decision_node_links", ["decision_id"])
    op.create_index("ix_decision_node_links_node", "decision_node_links", ["node_id"])


def downgrade() -> None:
    op.drop_index("ix_decision_node_links_node", table_name="decision_node_links")
    op.drop_index("ix_decision_node_links_decision", table_name="decision_node_links")
    op.drop_index("ix_decision_node_links_repo", table_name="decision_node_links")
    op.drop_table("decision_node_links")

    op.drop_index("ix_decision_edges_dst", table_name="decision_edges")
    op.drop_index("ix_decision_edges_src", table_name="decision_edges")
    op.drop_index("ix_decision_edges_repo", table_name="decision_edges")
    op.drop_table("decision_edges")
