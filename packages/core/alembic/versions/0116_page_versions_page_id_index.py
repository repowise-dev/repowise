"""Index wiki_page_versions.page_id, the foreign key every page delete checks.

Local SQLite gets the index from ``init_db``.

Revision ID: 0116
Revises: 0115
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers
revision: str = "0116"
down_revision: str | None = "0115"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_wiki_page_versions_page_id", "wiki_page_versions", ["page_id"])


def downgrade() -> None:
    op.drop_index("ix_wiki_page_versions_page_id", table_name="wiki_page_versions")
