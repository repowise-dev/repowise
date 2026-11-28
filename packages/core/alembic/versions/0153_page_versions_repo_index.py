"""Index wiki_page_versions on (repository_id, page_id).

Each ``update`` asks whether any of a repository's pages holds more versions
than ``wiki.page_versions_keep``; this index answers it without reading rows.

Local SQLite gets the index from ``init_db``.

Revision ID: 0153
Revises: 0152
Create Date: 2026-11-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers
revision: str = "0153"
down_revision: str | None = "0152"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # init_db may have created it from the models before this migration ran.
    op.create_index(
        "ix_wiki_page_versions_repo_page",
        "wiki_page_versions",
        ["repository_id", "page_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wiki_page_versions_repo_page", table_name="wiki_page_versions", if_exists=True
    )
