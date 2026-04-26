"""Add summary column to wiki_pages.

Stores a 1–3 sentence purpose blurb per page so MCP get_context can return
narrative file-level descriptions without shipping the full content_md to the
agent on every turn. Always populated (LLM-extracted in full mode, deterministic
in index-only mode).

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wiki_pages",
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("wiki_pages", "summary")
