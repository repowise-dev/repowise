"""Add human_notes column to wiki_pages for human-in-the-loop annotations.

Allows developers to attach notes, rationale, or context that survives
LLM-driven regeneration.  The column is nullable and preserved on upsert.

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("wiki_pages", sa.Column("human_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("wiki_pages", "human_notes")
