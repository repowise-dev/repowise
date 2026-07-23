"""Add hierarchy columns to wiki_pages so the outline lives in the data model.

Hierarchy used to be reassembled by every reader from page_type and
target_path prefixes, which meant the web app, the editor extension and the
MCP server each derived their own tree and could disagree. These four columns
let one tree be computed once at generation time and read everywhere.

All four are additive and optional: existing rows keep working with a null
parent and display_order 0, which reads as a flat wiki, exactly what those
rows describe today. Nothing 404s mid-rollout.

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No foreign key on parent_page_id: SQLite cannot add one to an existing
    # table, and the generated-page sweep deletes parents whose structural key
    # moved. The tree builder enforces consistency instead.
    op.add_column("wiki_pages", sa.Column("parent_page_id", sa.Text(), nullable=True))
    op.add_column(
        "wiki_pages",
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("wiki_pages", sa.Column("section_number", sa.Text(), nullable=True))
    op.add_column("wiki_pages", sa.Column("structural_key", sa.Text(), nullable=True))

    op.create_index("ix_wiki_pages_parent_page_id", "wiki_pages", ["parent_page_id"])
    op.create_index("ix_wiki_pages_structural_key", "wiki_pages", ["structural_key"])


def downgrade() -> None:
    op.drop_index("ix_wiki_pages_structural_key", table_name="wiki_pages")
    op.drop_index("ix_wiki_pages_parent_page_id", table_name="wiki_pages")
    op.drop_column("wiki_pages", "structural_key")
    op.drop_column("wiki_pages", "section_number")
    op.drop_column("wiki_pages", "display_order")
    op.drop_column("wiki_pages", "parent_page_id")
