"""Full-text search for repowise wiki pages.

Two backends:
- SQLite  (default): FTS5 virtual table ``page_fts``.
- PostgreSQL:        GIN index on ``to_tsvector('english', title || content)``.

The backend is detected automatically from the engine dialect.

Usage::

    fts = FullTextSearch(engine)
    await fts.ensure_index()           # idempotent — safe to call at startup
    await fts.index("id", "Title", "content …")
    results = await fts.search("decorator pattern", limit=5)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql import text


@dataclass
class SearchResult:
    """Unified result returned by vector search and full-text search."""

    page_id: str
    title: str
    page_type: str
    target_path: str
    score: float
    snippet: str
    search_type: Literal["vector", "fulltext"]


_SNIPPET_LEN = 200

# Common English stop words to strip from FTS queries
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "must",
        "can",
        "could",
        "am",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "about",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "we",
        "you",
        "he",
        "she",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "our",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "when",
        "where",
        "why",
        "not",
        "no",
        "so",
        "if",
        "or",
        "and",
        "but",
        "all",
        "each",
        "very",
        "just",
        "also",
        "than",
        "too",
        "only",
    }
)


def _build_fts5_query(query: str) -> str:
    """Build an FTS5 MATCH expression from a natural-language query.

    Strips stop words, then joins remaining terms with OR so that pages
    containing *any* keyword match.  Each term gets a ``*`` suffix for
    prefix matching (e.g. "pay*" matches "payment", "payload", etc.).
    Falls back to the raw (quoted) query when all tokens are stop words.
    """
    import re

    # Keep only alphanumeric tokens
    tokens = re.findall(r"[a-zA-Z0-9_]+", query.lower())
    meaningful = [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]

    if not meaningful:
        # All stop words — fall back to exact phrase
        safe = query.replace('"', '""')
        return f'"{safe}"'

    # FTS5: OR between prefix-match terms gives broad recall;
    # FTS5 rank (BM25) naturally boosts pages matching more terms.
    return " OR ".join(f'"{t}"*' for t in meaningful)


def _snippet(content: str) -> str:
    return content[:_SNIPPET_LEN].rstrip()


class FullTextSearch:
    """Full-text search backed by SQLite FTS5 or PostgreSQL tsvector."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._dialect = engine.dialect.name  # "sqlite" or "postgresql"

    async def ensure_index(self) -> None:
        """Create the FTS index if it does not exist (idempotent).

        For SQLite the FTS5 table is created here.
        For PostgreSQL the GIN index is created by the Alembic migration; this
        method is a no-op in that case.
        """
        if self._dialect == "sqlite":
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS page_fts "
                        "USING fts5(page_id UNINDEXED, title, content)"
                    )
                )
        elif self._dialect == "postgresql":
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_wiki_pages_fts "
                        "ON wiki_pages USING GIN("
                        "  to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(content, ''))"
                        ")"
                    )
                )

    async def index(self, page_id: str, title: str, content: str) -> None:
        """Add or replace a page in the FTS index."""
        if self._dialect == "sqlite":
            async with self._engine.begin() as conn:
                # FTS5 does not support UPDATE; use DELETE + INSERT
                await conn.execute(
                    text("DELETE FROM page_fts WHERE page_id = :pid"),
                    {"pid": page_id},
                )
                await conn.execute(
                    text(
                        "INSERT INTO page_fts(page_id, title, content) "
                        "VALUES (:pid, :title, :content)"
                    ),
                    {"pid": page_id, "title": title, "content": content},
                )
        # PostgreSQL: the GIN index on wiki_pages is maintained automatically
        # by the database as rows are inserted/updated via the CRUD layer.

    async def delete(self, page_id: str) -> None:
        """Remove a page from the FTS index."""
        if self._dialect == "sqlite":
            async with self._engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM page_fts WHERE page_id = :pid"),
                    {"pid": page_id},
                )

    async def delete_many(self, page_ids: list[str]) -> None:
        """Remove multiple pages from the FTS index in a single transaction."""
        if not page_ids:
            return
        if self._dialect == "sqlite":
            async with self._engine.begin() as conn:
                for pid in page_ids:
                    await conn.execute(
                        text("DELETE FROM page_fts WHERE page_id = :pid"),
                        {"pid": pid},
                    )
        # PostgreSQL: rows deleted via CASCADE automatically remove from GIN index.

    async def list_indexed_ids(self) -> set[str]:
        """Return the set of page IDs currently in the FTS index.

        Used by ``repowise doctor --repair`` to detect three-store
        inconsistencies.
        """
        if self._dialect == "sqlite":
            async with self._engine.connect() as conn:
                rows = await conn.execute(text("SELECT page_id FROM page_fts"))
                return {r[0] for r in rows.fetchall()}
        # PostgreSQL: all wiki_pages rows are automatically indexed via GIN,
        # so the set of "indexed" ids is all page ids in the table.
        async with self._engine.connect() as conn:
            rows = await conn.execute(text("SELECT id FROM wiki_pages"))
            return {r[0] for r in rows.fetchall()}

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search for pages matching *query*.

        Args:
            query: Natural-language search query.
            limit: Maximum number of results to return.

        Returns:
            List of SearchResult objects sorted by relevance (descending).
        """
        if not query.strip():
            return []

        if self._dialect == "sqlite":
            return await self._search_sqlite(query, limit)
        return await self._search_postgresql(query, limit)

    async def _search_sqlite(self, query: str, limit: int) -> list[SearchResult]:
        """FTS5 search.  ``rank`` is negative; we negate it to get a positive score."""
        fts_query = _build_fts5_query(query)

        async with self._engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT f.page_id, f.title, f.content, f.rank "
                    "FROM page_fts f "
                    "WHERE page_fts MATCH :q "
                    "ORDER BY rank "
                    "LIMIT :lim"
                ),
                {"q": fts_query, "lim": limit},
            )
            raw = rows.fetchall()

        # We need page_type and target_path from wiki_pages
        if not raw:
            return []

        page_ids = [r[0] for r in raw]
        rank_by_id = {r[0]: r[3] for r in raw}
        content_by_id = {r[0]: r[2] for r in raw}
        title_by_id = {r[0]: r[1] for r in raw}

        async with self._engine.connect() as conn:
            placeholders = ", ".join(f":id{i}" for i in range(len(page_ids)))
            params = {f"id{i}": pid for i, pid in enumerate(page_ids)}
            page_rows = await conn.execute(
                text(
                    f"SELECT id, page_type, target_path FROM wiki_pages "
                    f"WHERE id IN ({placeholders})"
                ),
                params,
            )
            meta = {r[0]: (r[1], r[2]) for r in page_rows.fetchall()}

        results = []
        for pid in page_ids:
            page_type, target_path = meta.get(pid, ("", ""))
            score = -(rank_by_id[pid] or 0.0)  # FTS5 rank is negative
            results.append(
                SearchResult(
                    page_id=pid,
                    title=title_by_id[pid],
                    page_type=page_type,
                    target_path=target_path,
                    score=score,
                    snippet=_snippet(content_by_id[pid]),
                    search_type="fulltext",
                )
            )
        return results

    async def _search_postgresql(self, query: str, limit: int) -> list[SearchResult]:
        """PostgreSQL tsvector search with ts_rank scoring."""
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT id, title, content, page_type, target_path, "
                    "  ts_rank("
                    "    to_tsvector('english', COALESCE(title,'') || ' ' || COALESCE(content,'')), "
                    "    plainto_tsquery('english', :q)"
                    "  ) AS rank "
                    "FROM wiki_pages "
                    "WHERE to_tsvector('english', COALESCE(title,'') || ' ' || COALESCE(content,'')) "
                    "  @@ plainto_tsquery('english', :q) "
                    "ORDER BY rank DESC "
                    "LIMIT :lim",
                ),
                {"q": query, "lim": limit},
            )
            raw = rows.fetchall()

        return [
            SearchResult(
                page_id=r[0],
                title=r[1],
                page_type=r[3],
                target_path=r[4],
                score=float(r[5]),
                snippet=_snippet(r[2]),
                search_type="fulltext",
            )
            for r in raw
        ]
