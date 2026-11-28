"""Key wiki_pages by (repository_id, id); versions refer to a page by the pair.

A page id (``file_page:src/main.py``) names no repository, so repositories
sharing one database overwrote each other's pages. The primary key becomes
``(repository_id, id)``, ``wiki_page_versions`` gets a composite foreign key,
and ``ix_wiki_pages_id`` serves the reads that start from an id alone.

The work lives in ``repowise.core.persistence.page_identity_migration``, which
``init_db`` also runs for a store that never ran Alembic. Downgrade refuses
while two repositories hold the same page id.

Pages the old key let one repository overwrite keep the content they hold
now; re-index the affected repositories to restore their own pages.

Revision ID: 0156
Revises: 0155
Create Date: 2026-11-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from repowise.core.persistence.page_identity_migration import rekey_pages, unkey_pages

# revision identifiers
revision: str = "0156"
down_revision: str | None = "0155"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    rekey_pages(op.get_bind())


def downgrade() -> None:
    unkey_pages(op.get_bind())
