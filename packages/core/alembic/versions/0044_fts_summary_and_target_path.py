"""Full-text search covers a page's summary and target path.

Two fields already decide whether a page is the right answer and could not
cause it to be found. ``summary`` is a fresh LLM paraphrase written for every
page — never a prefix of the prose it paraphrases — and the answer tool's
coverage re-ranker reads it when ordering results, so it reorders candidates it
cannot produce. ``target_path`` is the file the page documents; most page
titles are that path verbatim, so a question that names a directory or a file
matched only whatever the generated prose happened to mention.

PostgreSQL: the GIN index is on an expression, so widening the searchable text
means dropping the index and rebuilding it over the wider expression. The rows
themselves do not move. ``search.py`` builds the identical expression at query
time — if the two ever drift the planner silently stops using the index and
falls back to a sequential scan, which still returns the right rows and is why
the expression is defined in one place and read from there.

SQLite: FTS5 has no ALTER TABLE, so the virtual table is dropped and refilled
from ``wiki_pages``, which is the system of record for all four indexed fields.
The old index could not supply the new columns — it never held them.

``FullTextSearch.ensure_index`` performs the same upgrade at runtime, because
most local stores are created by ``init_db`` and never see Alembic at all. Both
paths are idempotent and converge on the same shape.

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from repowise.core.persistence.search import PAGE_FTS_DDL, PG_FTS_EXPRESSION

# revision identifiers
revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_PG_EXPRESSION = "to_tsvector('english', COALESCE(title,'') || ' ' || COALESCE(content,''))"

_REFILL_SQL = (
    "INSERT INTO page_fts(page_id, title, content, summary, target_path) "
    "SELECT id, COALESCE(title,''), COALESCE(content,''), "
    "       COALESCE(summary,''), COALESCE(target_path,'') "
    "FROM wiki_pages"
)

_OLD_REFILL_SQL = (
    "INSERT INTO page_fts(page_id, title, content) "
    "SELECT id, COALESCE(title,''), COALESCE(content,'') FROM wiki_pages"
)

_OLD_SQLITE_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(page_id UNINDEXED, title, content)"
)


def upgrade() -> None:
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
