"""Initial repowise schema.

Creates all 8 tables.  For PostgreSQL also:
- Installs the pgvector extension.
- Adds an ``embedding vector(1536)`` column to wiki_pages.
- Creates a GIN index for full-text search.

For SQLite:
- Creates the FTS5 virtual table for full-text search.

Revision ID: 0001
Revises: None
Create Date: 2026-03-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # repositories
    # ------------------------------------------------------------------
    op.create_table(
        "repositories",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.Text, nullable=False, server_default=""),
        sa.Column("local_path", sa.Text, nullable=False),
        sa.Column("default_branch", sa.String(255), nullable=False, server_default="main"),
        sa.Column("head_commit", sa.String(40), nullable=True),
        sa.Column("settings_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ------------------------------------------------------------------
    # generation_jobs
    # ------------------------------------------------------------------
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("provider_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("model_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("total_pages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completed_pages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_pages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("current_level", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("config_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ------------------------------------------------------------------
    # wiki_pages
    # ------------------------------------------------------------------
    op.create_table(
        "wiki_pages",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_type", sa.String(64), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("target_path", sa.Text, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("generation_level", sa.Integer, nullable=False, server_default="0"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column(
            "freshness_status", sa.String(32), nullable=False, server_default="fresh"
        ),
        sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ------------------------------------------------------------------
    # wiki_page_versions
    # ------------------------------------------------------------------
    op.create_table(
        "wiki_page_versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "page_id",
            sa.Text,
            sa.ForeignKey("wiki_pages.id"),
            nullable=False,
        ),
        sa.Column("repository_id", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("page_type", sa.String(64), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ------------------------------------------------------------------
    # graph_nodes
    # ------------------------------------------------------------------
    op.create_table(
        "graph_nodes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.Text, nullable=False),
        sa.Column("node_type", sa.String(32), nullable=False, server_default="file"),
        sa.Column("language", sa.String(32), nullable=False, server_default=""),
        sa.Column("symbol_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("has_error", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("is_test", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("is_entry_point", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("pagerank", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("betweenness", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("community_id", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("repository_id", "node_id", name="uq_graph_node"),
    )

    # ------------------------------------------------------------------
    # graph_edges
    # ------------------------------------------------------------------
    op.create_table(
        "graph_edges",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_node_id", sa.Text, nullable=False),
        sa.Column("target_node_id", sa.Text, nullable=False),
        sa.Column("imported_names_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "repository_id", "source_node_id", "target_node_id", name="uq_graph_edge"
        ),
    )

    # ------------------------------------------------------------------
    # webhook_events
    # ------------------------------------------------------------------
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("delivery_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("payload_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("processed", sa.Boolean, nullable=False, server_default="0"),
        sa.Column(
            "job_id",
            sa.String(32),
            sa.ForeignKey("generation_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ------------------------------------------------------------------
    # wiki_symbols
    # ------------------------------------------------------------------
    op.create_table(
        "wiki_symbols",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("symbol_id", sa.Text, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("qualified_name", sa.Text, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("signature", sa.Text, nullable=False, server_default=""),
        sa.Column("start_line", sa.Integer, nullable=False, server_default="0"),
        sa.Column("end_line", sa.Integer, nullable=False, server_default="0"),
        sa.Column("docstring", sa.Text, nullable=True),
        sa.Column(
            "visibility", sa.String(16), nullable=False, server_default="public"
        ),
        sa.Column("is_async", sa.Boolean, nullable=False, server_default="0"),
        sa.Column(
            "complexity_estimate", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("language", sa.String(32), nullable=False, server_default=""),
        sa.Column("parent_name", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("repository_id", "symbol_id", name="uq_wiki_symbol"),
    )

    # ------------------------------------------------------------------
    # Dialect-specific extras
    # ------------------------------------------------------------------
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Install pgvector extension and add embedding column
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.add_column("wiki_pages", sa.Column("embedding", sa.Text, nullable=True))
        op.execute(
            "ALTER TABLE wiki_pages "
            "ALTER COLUMN embedding TYPE vector(1536) "
            "USING NULL::vector(1536)"
        )
        # GIN index for full-text search (maintained automatically)
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_wiki_pages_fts ON wiki_pages "
            "USING GIN("
            "  to_tsvector('english', COALESCE(title,'') || ' ' || COALESCE(content,''))"
            ")"
        )

    elif dialect == "sqlite":
        # FTS5 virtual table for full-text search (SQLite only)
        op.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS page_fts "
            "USING fts5(page_id UNINDEXED, title, content)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        op.execute("DROP TABLE IF EXISTS page_fts")
    elif dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_wiki_pages_fts")

    op.drop_table("wiki_symbols")
    op.drop_table("webhook_events")
    op.drop_table("graph_edges")
    op.drop_table("graph_nodes")
    op.drop_table("wiki_page_versions")
    op.drop_table("wiki_pages")
    op.drop_table("generation_jobs")
    op.drop_table("repositories")
