"""Add external_systems table and graph_nodes.external_system_id FK.

Powers C4 L1 (System Context) by persisting third-party dependencies parsed
from repo manifests (package.json, pyproject.toml, Cargo.toml, go.mod,
.csproj). Each GraphNode that represents an `external:*` import can be linked
to a row here so renderers know the dep's name, version, category, ecosystem.

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_systems",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("ecosystem", sa.String(32), nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="library"),
        sa.Column("version", sa.String(64), nullable=True),
        sa.Column("declared_in", sa.Text(), nullable=False),
        sa.Column("is_dev_dep", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "repository_id", "name", "declared_in", name="uq_external_system"
        ),
    )
    op.create_index(
        "ix_external_systems_repository_id",
        "external_systems",
        ["repository_id"],
    )

    with op.batch_alter_table("graph_nodes") as batch_op:
        batch_op.add_column(
            sa.Column("external_system_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_graph_nodes_external_system",
            "external_systems",
            ["external_system_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_graph_nodes_external_system_id",
        "graph_nodes",
        ["external_system_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_graph_nodes_external_system_id", table_name="graph_nodes")
    with op.batch_alter_table("graph_nodes") as batch_op:
        batch_op.drop_constraint("fk_graph_nodes_external_system", type_="foreignkey")
        batch_op.drop_column("external_system_id")

    op.drop_index("ix_external_systems_repository_id", table_name="external_systems")
    op.drop_table("external_systems")
