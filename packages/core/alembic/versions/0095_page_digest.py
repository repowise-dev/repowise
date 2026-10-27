"""wiki_pages: an agent digest beside the page body.

``digest`` holds what a page carries for search and agents but not for a
reader: the questions it answers, its identifiers, public API and git signals.
It used to be appended to ``content``, which the reader, full-text search,
embeddings and MCP all read as one string.

Full-text search indexes the new column, so the PostgreSQL GIN expression is
rebuilt over it and the SQLite FTS5 table is dropped and refilled. Local SQLite
stores get the column from ``init_db``'s additive reconciler and the FTS table
from ``FullTextSearch.ensure_index``; this migration covers PostgreSQL.

Revision ID: 0095
Revises: 0094
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Frozen at this revision's shape, never imported from ``search.py``: the next
# widening must not change what this revision creates.
PG_FTS_EXPRESSION = (
    "to_tsvector('english', "
    "COALESCE(title,'') || ' ' || COALESCE(content,'') || ' ' "
    "|| COALESCE(summary,'') || ' ' || COALESCE(target_path,'') || ' ' "
    "|| COALESCE(digest,''))"
)
# SQLite: the file vocabulary column predates this revision (added at runtime
# by ``FullTextSearch.ensure_index``), so both shapes keep it.
_VOCABULARY_SQL = (
    "CASE WHEN json_valid(metadata_json) "
    "THEN COALESCE(json_extract(metadata_json, '$.file_vocabulary'), '') "
    "ELSE '' END"
)
PAGE_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS page_fts "
    "USING fts5(page_id UNINDEXED, title, content, summary, target_path, vocabulary, digest)"
)

# revision identifiers
revision: str = "0095"
down_revision: str | None = "0094"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_PG_EXPRESSION = (
    "to_tsvector('english', "
    "COALESCE(title,'') || ' ' || COALESCE(content,'') || ' ' "
    "|| COALESCE(summary,'') || ' ' || COALESCE(target_path,''))"
)

_REFILL_SQL = (
    "INSERT INTO page_fts"
    "(page_id, title, content, summary, target_path, vocabulary, digest) "
    "SELECT id, COALESCE(title,''), COALESCE(content,''), "
    "       COALESCE(summary,''), COALESCE(target_path,''), "
    f"      {_VOCABULARY_SQL}, COALESCE(digest,'') "
    "FROM wiki_pages"
)

_OLD_SQLITE_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS page_fts "
    "USING fts5(page_id UNINDEXED, title, content, summary, target_path, vocabulary)"
)

_OLD_REFILL_SQL = (
    "INSERT INTO page_fts(page_id, title, content, summary, target_path, vocabulary) "
    "SELECT id, COALESCE(title,''), COALESCE(content,''), "
    "       COALESCE(summary,''), COALESCE(target_path,''), "
    f"      {_VOCABULARY_SQL} "
    "FROM wiki_pages"
)

def upgrade() -> None:
    op.add_column(
        "wiki_pages",
        sa.Column("digest", sa.Text(), nullable=False, server_default=""),
    )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_wiki_pages_fts")
        op.execute(f"CREATE INDEX idx_wiki_pages_fts ON wiki_pages USING GIN({PG_FTS_EXPRESSION})")
    elif dialect == "sqlite":
        op.execute("DROP TABLE IF EXISTS page_fts")
        op.execute(PAGE_FTS_DDL)
        op.execute(_REFILL_SQL)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_wiki_pages_fts")
        op.execute(f"CREATE INDEX idx_wiki_pages_fts ON wiki_pages USING GIN({_OLD_PG_EXPRESSION})")
    elif dialect == "sqlite":
        op.execute("DROP TABLE IF EXISTS page_fts")
        op.execute(_OLD_SQLITE_DDL)
        op.execute(_OLD_REFILL_SQL)
    op.drop_column("wiki_pages", "digest")
