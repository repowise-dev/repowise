"""Full-text search for repowise wiki pages.

Two backends:
- SQLite  (default): FTS5 virtual table ``page_fts``.
- PostgreSQL:        GIN index on ``to_tsvector('english', title || content)``.

The backend is detected automatically from the engine dialect.

Usage::

    fts = FullTextSearch(engine)
    await fts.ensure_index()           # idempotent — safe to call at startup
    await fts.index("id", "Title", "content …", summary="…", target_path="a/b.py")
    results = await fts.search("decorator pattern", limit=5)
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql import text

from .information_floor import information_floor, meets_information_floor, substantive_text


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

# Every column of ``page_fts``, in table order. ``page_id`` is stored but not
# indexed — it is the join key, never a thing to match on.
#
# ``summary`` and ``target_path`` are indexed because both decide whether a
# page is the right answer while being unable to make it a candidate. The
# summary is a fresh LLM paraphrase, written for every page and never a prefix
# of the prose it paraphrases, and the answer tool's coverage re-ranker already
# reads it — so it reorders results it cannot produce. The target path is the
# file the page documents; most page titles are that path verbatim, which
# leaves a question naming a directory matching only whatever the generated
# prose happens to mention.
PAGE_FTS_COLUMNS = ("page_id", "title", "content", "summary", "target_path")

PAGE_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS page_fts "
    "USING fts5(page_id UNINDEXED, title, content, summary, target_path)"
)

# The text PostgreSQL indexes and searches. The GIN index built by the Alembic
# migration uses this exact expression: any drift between the two silently
# drops the index from the query plan, leaving a sequential scan that still
# returns the right rows, so both read it from here.
PG_FTS_EXPRESSION = (
    "to_tsvector('english', "
    "COALESCE(title,'') || ' ' || COALESCE(content,'') || ' ' "
    "|| COALESCE(summary,'') || ' ' || COALESCE(target_path,''))"
)

# Shortest term that still gets a prefix wildcard. See :func:`_match_term`.
_PREFIX_MIN_CHARS = 4

# A term matching more than this share of the corpus is dropped from the query
# expression: it cannot discriminate between pages, and every one of them drags
# thousands of candidates in for BM25 to sort out afterwards. Env-tunable
# because the right ceiling is a property of a corpus's vocabulary, and the only
# honest way to set it is to measure recall on the corpus in question.
_DF_CEILING = float(os.environ.get("REPOWISE_FTS_DF_CEILING", "0.20"))

# Never let the ceiling empty a query. A question written entirely out of common
# words keeps its rarest terms — a thin match beats no match, especially since
# vector retrieval is answering the same question alongside this.
_MIN_KEPT_TERMS = 3

# How many distinct terms' document frequencies one FullTextSearch keeps. Term
# frequency changes only when the corpus is rewritten, and questions reuse
# vocabulary heavily, so a small cache removes almost all of the counting.
_DF_CACHE_MAX = 2048

# Keeps scores strictly decreasing when two MATCH expressions' results are
# concatenated. Small enough not to disturb any threshold downstream.
_SCORE_EPSILON = 1e-6

_log = logging.getLogger(__name__)

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


def _meaningful_terms(query: str) -> list[str]:
    """Alphanumeric tokens of *query*, minus stop words and single characters."""
    import re

    tokens = re.findall(r"[a-zA-Z0-9_]+", query.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


def _match_term(term: str) -> str:
    """One FTS5 term, prefix-matched only when it is long enough to be specific.

    A prefix wildcard is what earns morphological recall — ``invalidat*`` finds
    "invalidates" and "invalidation" — and it is also what turns a short token
    into a corpus-wide match: ``set*`` hits settings, setup, setter, setdefault.
    Below the cut the term is matched exactly, which costs the odd plural and
    buys back a candidate set that BM25 was being asked to sort out afterwards.
    """
    return f'"{term}"*' if len(term) >= _PREFIX_MIN_CHARS else f'"{term}"'


def _pg_term(term: str) -> str:
    """One ``to_tsquery`` lexeme, prefix-matched on the same rule as FTS5.

    Terms reach here already reduced to ``[a-zA-Z0-9_]+`` by
    :func:`_meaningful_terms`, so there is no quoting to get wrong — anything
    that could change the expression's shape has been dropped before this.
    """
    return f"{term}:*" if len(term) >= _PREFIX_MIN_CHARS else term


def _build_fts5_query(query: str, document_frequency: Callable[[str], int] | None = None) -> str:
    """Build an FTS5 MATCH expression from a natural-language query.

    Terms are OR-ed, because a developer question is a paraphrase of the page
    that answers it: its rarest words are frequently the asker's vocabulary
    rather than the page's, so requiring all of them (AND) excludes the right
    page far more often than it narrows usefully. Measured on a 3,678-page
    corpus, AND over the meaningful terms returned nothing at all for 65 of 99
    questions, and held the expected page for 25 of 99 against 99 of 99 for OR.

    What OR needs is not a different operator but fewer junk terms. A question
    carries words that are not stop words yet still match a large share of any
    code corpus ("file", "does", "page", "index"), and each one drags in
    thousands of pages that only BM25 tie-breaking then has to sort out. Given
    a *document_frequency* callable, terms matching more than
    :data:`_DF_CEILING` of the corpus are dropped from the expression; the same
    measurement puts the median candidate set at 20% of the corpus rather than
    65%. Without the callable the expression keeps every term, which is the
    prior behaviour.

    At least :data:`_MIN_KEPT_TERMS` terms always survive — the rarest ones —
    so a question written entirely from common words still matches something.
    """
    meaningful = _meaningful_terms(query)

    if not meaningful:
        # All stop words — fall back to exact phrase
        safe = query.replace('"', '""')
        return f'"{safe}"'

    kept = meaningful
    if document_frequency is not None:
        total = document_frequency("")  # corpus size, by convention
        if total > 0:
            ceiling = _DF_CEILING * total
            selective = [t for t in meaningful if document_frequency(t) <= ceiling]
            if len(selective) < _MIN_KEPT_TERMS:
                # Every term is common. Keep the rarest few rather than none:
                # a thin match beats an empty one, and the vector arm is still
                # answering alongside this.
                selective = sorted(meaningful, key=document_frequency)[:_MIN_KEPT_TERMS]
            kept = selective

    return " OR ".join(_match_term(t) for t in kept)


def snippet_around(content: str, query: str, length: int = _SNIPPET_LEN) -> str:
    """A window of *content* centred on the first term of *query* it carries.

    This string is a hit's evidence: ``search_codebase`` returns it as the
    reason the page is in the list, and the answer tool's coverage re-ranker
    reads it when ordering candidates. Taken from the front of the page it is
    the same ``## Overview`` opener on every generated page — identical across
    thousands of hits, and never the passage that matched.

    Terms are the ones :func:`_meaningful_terms` keeps, so a query made of
    common words cannot steer the window: matching on "the" would centre on
    the opener for almost every query and the window would buy nothing.

    Falls back to the opener when nothing matches, which is the right answer
    for a hit found by its title or its path — those carry no region of the
    content to point at. Never returns empty for a non-empty page.
    """
    if not content:
        return ""

    lowered = content.lower()
    position = -1
    for term in _meaningful_terms(query):
        found = lowered.find(term)
        if found != -1 and (position == -1 or found < position):
            position = found
    if position == -1:
        return content[:length].rstrip()

    # Centre the window, then slide it back inside the page rather than
    # letting it run off an edge: a match in the first or last few characters
    # would otherwise return a window shorter than the budget for no reason.
    start = max(0, position - length // 2)
    start = min(start, max(0, len(content) - length))
    return content[start : start + length].strip()


class FullTextSearch:
    """Full-text search backed by SQLite FTS5 or PostgreSQL tsvector."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._dialect = engine.dialect.name  # "sqlite" or "postgresql"
        # term → how many pages it matches. "" is the corpus size.
        self._df_cache: OrderedDict[str, int] = OrderedDict()
        self._warned_missing_fields = False
        # Pages this instance kept out of the index for saying too little.
        # A counter, not a line per page: a corpus-wide write would print
        # thousands. The total is what says whether the floor is set sanely.
        self.skipped_below_floor = 0
        self._floor_sample: list[str] = []

    async def ensure_index(self) -> None:
        """Create the FTS index if it does not exist (idempotent).

        For SQLite the FTS5 table is created here, and an index left on an
        older column set by a previous repowise is rebuilt — see
        :meth:`_upgrade_sqlite_schema`.
        For PostgreSQL the GIN index is created by the Alembic migration; this
        recreates it when absent, which is what a store built before the
        migration existed needs.
        """
        if self._dialect == "sqlite":
            async with self._engine.begin() as conn:
                await conn.execute(text(PAGE_FTS_DDL))
            await self._upgrade_sqlite_schema()
        elif self._dialect == "postgresql":
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_wiki_pages_fts "
                        f"ON wiki_pages USING GIN({PG_FTS_EXPRESSION})"
                    )
                )

    async def _upgrade_sqlite_schema(self) -> None:
        """Rebuild ``page_fts`` when it predates a column, refilling from SQL.

        FTS5 has no ``ALTER TABLE``, so widening the index means dropping it
        and writing every row again. The rows themselves cannot supply the new
        columns — the old index never held them — so the refill reads
        ``wiki_pages``, which is the system of record for all four indexed
        fields.

        The table is created with ``IF NOT EXISTS`` by three separate callers,
        so without this an upgraded install would keep its old shape
        indefinitely and quietly search fewer fields than it writes.
        """
        async with self._engine.connect() as conn:
            info = await conn.execute(text("PRAGMA table_info(page_fts)"))
            columns = [r[1] for r in info.fetchall()]
            if list(columns) == list(PAGE_FTS_COLUMNS):
                return
            missing = [c for c in PAGE_FTS_COLUMNS if c not in columns]
            if not missing:
                # Extra or reordered columns are somebody else's table. Refuse
                # rather than drop data this class did not write.
                raise RuntimeError(
                    f"page_fts has unexpected columns {columns!r}; "
                    f"expected {list(PAGE_FTS_COLUMNS)!r}"
                )

            indexed_rows = await conn.execute(text("SELECT count(*) FROM page_fts"))
            indexed_count = int(indexed_rows.scalar() or 0)
            page_rows = await conn.execute(text("SELECT count(*) FROM wiki_pages"))
            page_count = int(page_rows.scalar() or 0)

        # The rebuild is a delete followed by a refill from a different table.
        # If that table cannot account for what is already indexed, the two
        # halves of the store have drifted and refilling would delete
        # searchable pages outright. An index on the old shape still answers
        # queries, so leaving it alone is strictly the safer failure.
        if indexed_count > page_count:
            raise RuntimeError(
                f"Refusing to rebuild page_fts: {indexed_count} indexed rows but only "
                f"{page_count} rows in wiki_pages to refill them from. The full-text "
                f"index and the page table have drifted apart; repair the store "
                f"(repowise doctor --repair) before upgrading it."
            )

        _log.info(
            "Rebuilding page_fts to add %s (%d pages to reindex)",
            ", ".join(missing),
            page_count,
        )
        async with self._engine.begin() as conn:
            await conn.execute(text("DROP TABLE page_fts"))
            await conn.execute(text(PAGE_FTS_DDL))
            await conn.execute(
                text(
                    "INSERT INTO page_fts(page_id, title, content, summary, target_path) "
                    "SELECT id, COALESCE(title,''), COALESCE(content,''), "
                    "       COALESCE(summary,''), COALESCE(target_path,'') "
                    "FROM wiki_pages"
                )
            )
            written = await conn.execute(text("SELECT count(*) FROM page_fts"))
            refilled = int(written.scalar() or 0)

        if refilled != page_count:
            # Nothing in the statement above should be able to drop a row, so
            # a mismatch means the page table moved underneath the rebuild.
            _log.warning("page_fts rebuild wrote %d rows for %d pages", refilled, page_count)

    async def index(
        self,
        page_id: str,
        title: str,
        content: str,
        summary: str | None = None,
        target_path: str | None = None,
    ) -> None:
        """Add or replace a page in the FTS index.

        *summary* and *target_path* are optional so a caller written against
        the older three-column index keeps working, but omitting them is
        logged: the row it writes looks healthy and can never be found by
        either field, which is a ranking regression with no symptom. Nine
        places inside repowise write this index and every one of them passes
        both — an omission is a call site that was missed, not a choice.

        An empty *string* is a different thing and is not warned about: some
        page types genuinely have no summary.

        A page below the information floor is not indexed, and any row it
        already had is deleted. Ten places write this index, so the decision
        lives here rather than at each of them — one that skipped the check
        would keep re-admitting the pages the others exclude, and nothing
        would report the disagreement. The page itself is untouched: it stays
        in ``wiki_pages`` and stays a valid link target.
        """
        if summary is None or target_path is None:
            self._warn_missing_index_fields(page_id, summary, target_path)
            summary = summary if summary is not None else ""
            target_path = target_path if target_path is not None else ""

        indexable = meets_information_floor(content)
        if not indexable:
            self._count_skipped_below_floor(page_id, content)

        if self._dialect == "sqlite":
            async with self._engine.begin() as conn:
                # FTS5 does not support UPDATE; use DELETE + INSERT. The delete
                # runs either way: a page that has fallen below the floor since
                # its last index must lose the row it had, or the exclusion
                # only ever applies to pages that never existed.
                await conn.execute(
                    text("DELETE FROM page_fts WHERE page_id = :pid"),
                    {"pid": page_id},
                )
                if not indexable:
                    return
                await conn.execute(
                    text(
                        "INSERT INTO page_fts(page_id, title, content, summary, target_path) "
                        "VALUES (:pid, :title, :content, :summary, :target_path)"
                    ),
                    {
                        "pid": page_id,
                        "title": title,
                        "content": content,
                        "summary": summary,
                        "target_path": target_path,
                    },
                )
        # PostgreSQL: the GIN index on wiki_pages is maintained automatically
        # by the database as rows are inserted/updated via the CRUD layer.

    def _count_skipped_below_floor(self, page_id: str, content: str) -> None:
        """Record a page held out of the index, and name the first few."""
        self.skipped_below_floor += 1
        if len(self._floor_sample) < 3:
            self._floor_sample.append(page_id)
        if self.skipped_below_floor == 1:
            _log.info(
                "fts_page_below_information_floor page_id=%s substantive_chars=%d floor=%d",
                page_id,
                len(substantive_text(content)),
                information_floor(),
            )

    def log_floor_exclusions(self) -> None:
        """Report the run's total. Callers that index a corpus call this once.

        Silent when nothing was excluded, so a repository whose pages are all
        substantial says nothing rather than reporting a zero.
        """
        if not self.skipped_below_floor:
            return
        _log.info(
            "fts_pages_below_information_floor count=%d floor=%d sample=%s",
            self.skipped_below_floor,
            information_floor(),
            ", ".join(self._floor_sample),
        )

    def _warn_missing_index_fields(
        self, page_id: str, summary: str | None, target_path: str | None
    ) -> None:
        """Log the first caller that indexes a page without the newer fields.

        Once per instance, because a caller inside a loop would otherwise
        write one line per page in the corpus.
        """
        if self._warned_missing_fields:
            return
        self._warned_missing_fields = True
        omitted = [
            name
            for name, value in (("summary", summary), ("target_path", target_path))
            if value is None
        ]
        _log.warning(
            "Indexing %r without %s — those pages will not be findable by the "
            "omitted field. This is a call site that needs updating.",
            page_id,
            " or ".join(omitted),
        )

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
            # Chunk to stay under SQLite's default 999-parameter limit per statement.
            chunk_size = 500
            async with self._engine.begin() as conn:
                for start in range(0, len(page_ids), chunk_size):
                    chunk = page_ids[start : start + chunk_size]
                    placeholders = ", ".join(f":p{i}" for i in range(len(chunk)))
                    params = {f"p{i}": pid for i, pid in enumerate(chunk)}
                    await conn.execute(
                        text(f"DELETE FROM page_fts WHERE page_id IN ({placeholders})"),
                        params,
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

    async def _document_frequency(self, conn, term: str) -> int:
        """How many indexed pages *term* matches. ``""`` is the corpus size.

        Cached per instance: a term's frequency only moves when the corpus is
        rewritten, and questions reuse vocabulary heavily, so the count query
        is paid roughly once per distinct word this process ever sees.
        """
        if term in self._df_cache:
            self._df_cache.move_to_end(term)
            return self._df_cache[term]

        if term:
            sql = "SELECT count(*) FROM page_fts WHERE page_fts MATCH :q"
            params = {"q": _match_term(term)}
        else:
            sql = "SELECT count(*) FROM page_fts"
            params = {}
        try:
            row = await conn.execute(text(sql), params)
            count = int(row.scalar() or 0)
        except Exception:
            # A term FTS5 refuses to parse must not fail the search: treat it as
            # rare so it stays in the expression, where it either matches or is
            # rejected there instead.
            _log.warning("Could not count pages matching %r", term, exc_info=True)
            count = 0

        self._df_cache[term] = count
        while len(self._df_cache) > _DF_CACHE_MAX:
            self._df_cache.popitem(last=False)
        return count

    async def _build_selective_query(self, query: str, df) -> str:
        """``_build_fts5_query`` with this corpus's term frequencies resolved.

        The builder stays a pure synchronous function — it is the piece worth
        testing on its own — so the counts it needs are awaited here and handed
        over as a plain lookup.
        """
        terms = _meaningful_terms(query)
        if not terms:
            return _build_fts5_query(query)
        counts = {"": await df("")}
        for term in terms:
            if term not in counts:
                counts[term] = await df(term)
        return _build_fts5_query(query, counts.__getitem__)

    async def _matching_rows(self, conn, fts_query: str, limit: int) -> list:
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
        return list(rows.fetchall())

    async def _search_sqlite(self, query: str, limit: int) -> list[SearchResult]:
        """FTS5 search.  ``rank`` is negative; we negate it to get a positive score."""
        async with self._engine.connect() as conn:

            async def df(term: str) -> int:
                return await self._document_frequency(conn, term)

            fts_query = await self._build_selective_query(query, df)
            raw = await self._matching_rows(conn, fts_query, limit)

            # The frequency ceiling can cut a question down to terms that
            # nothing carries together. Retrying with every term is the prior
            # behaviour, so the narrowing can lose ranking precision but never
            # the only hits there were.
            if len(raw) < limit:
                widened = _build_fts5_query(query)
                if widened != fts_query:
                    seen = {r[0] for r in raw}
                    extra = [
                        r
                        for r in await self._matching_rows(conn, widened, limit)
                        if r[0] not in seen
                    ]
                    if extra:
                        _log.debug(
                            "FTS widened from %d to %d hits after the frequency ceiling "
                            "left too few",
                            len(raw),
                            len(raw) + len(extra),
                        )
                    raw = (raw + extra)[:limit]

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
        # Rows can come from two MATCH expressions, whose BM25 scales are not
        # comparable. Order is authoritative — the narrow pass ranks ahead of
        # the widened one — so scores are clamped to be non-increasing along it,
        # keeping a caller that sorts by score in agreement with one that reads
        # the list in order.
        ceiling: float | None = None
        for pid in page_ids:
            page_type, target_path = meta.get(pid, ("", ""))
            score = -(rank_by_id[pid] or 0.0)  # FTS5 rank is negative
            if ceiling is not None and score > ceiling:
                score = ceiling
            ceiling = score - _SCORE_EPSILON
            results.append(
                SearchResult(
                    page_id=pid,
                    title=title_by_id[pid],
                    page_type=page_type,
                    target_path=target_path,
                    score=score,
                    snippet=snippet_around(content_by_id[pid], query),
                    search_type="fulltext",
                )
            )
        return results

    async def _pg_document_frequency(self, conn, term: str) -> int:
        """Pages matching *term* in PostgreSQL. ``""`` is the corpus size.

        Shares :attr:`_df_cache` with the SQLite path; an instance only ever
        talks to one backend.
        """
        if term in self._df_cache:
            self._df_cache.move_to_end(term)
            return self._df_cache[term]

        if term:
            sql = (
                f"SELECT count(*) FROM wiki_pages "
                f"WHERE {PG_FTS_EXPRESSION} @@ to_tsquery('english', :q)"
            )
            params = {"q": _pg_term(term)}
        else:
            sql = "SELECT count(*) FROM wiki_pages"
            params = {}
        try:
            row = await conn.execute(text(sql), params)
            count = int(row.scalar() or 0)
        except Exception:
            _log.warning("Could not count pages matching %r", term, exc_info=True)
            count = 0

        self._df_cache[term] = count
        while len(self._df_cache) > _DF_CACHE_MAX:
            self._df_cache.popitem(last=False)
        return count

    async def _build_ts_query(self, conn, query: str) -> str:
        """OR-joined ``to_tsquery`` expression over this query's kept terms."""
        terms = _meaningful_terms(query)
        if not terms:
            return ""
        total = await self._pg_document_frequency(conn, "")
        kept = terms
        if total > 0:
            selective = []
            for term in terms:
                if await self._pg_document_frequency(conn, term) <= _DF_CEILING * total:
                    selective.append(term)
            if len(selective) < _MIN_KEPT_TERMS:
                counts = {t: await self._pg_document_frequency(conn, t) for t in terms}
                selective = sorted(terms, key=counts.__getitem__)[:_MIN_KEPT_TERMS]
            kept = selective
        return " | ".join(_pg_term(t) for t in kept)

    async def _search_postgresql(self, query: str, limit: int) -> list[SearchResult]:
        """PostgreSQL tsvector search with ts_rank scoring.

        The query used to be handed to ``plainto_tsquery`` whole, which strips
        nothing the caller intended and — more consequentially — joins every
        remaining lexeme with AND. A developer question is a paraphrase, so
        requiring all of its words matches almost nothing: on a 3,678-page
        corpus, AND over the meaningful terms of 99 questions returned no rows
        at all for 65 of them. This backend was therefore the strict mirror
        image of SQLite's, which matched two thirds of the corpus, and neither
        was doing retrieval.

        Both now build the same expression from the same terms with the same
        frequency ceiling, so a hosted index and a local one rank a question the
        same way.
        """
        async with self._engine.connect() as conn:
            ts_query = await self._build_ts_query(conn, query)
            if not ts_query:
                return []
            rows = await conn.execute(
                text(
                    f"SELECT id, title, content, page_type, target_path, "
                    f"  ts_rank({PG_FTS_EXPRESSION}, to_tsquery('english', :q)) AS rank "
                    f"FROM wiki_pages "
                    f"WHERE {PG_FTS_EXPRESSION} @@ to_tsquery('english', :q) "
                    f"ORDER BY rank DESC "
                    f"LIMIT :lim",
                ),
                {"q": ts_query, "lim": limit},
            )
            raw = rows.fetchall()

        return [
            SearchResult(
                page_id=r[0],
                title=r[1],
                page_type=r[3],
                target_path=r[4],
                score=float(r[5]),
                snippet=snippet_around(r[2], query),
                search_type="fulltext",
            )
            for r in raw
        ]
