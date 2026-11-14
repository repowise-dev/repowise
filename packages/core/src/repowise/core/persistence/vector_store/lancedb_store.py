"""LanceDB-backed vector store (embedded, local file storage)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from repowise.core.providers.embedding.base import Embedder

from ..search import _SNIPPET_LEN, SearchResult, snippet_around
from ._base import (
    STORED_SNIPPET_CHARS,
    BatchChunkFailure,
    BatchEmbeddingError,
    PageChunks,
    VectorCoverage,
    VectorStore,
    embed_page_chunks,
    iter_page_chunk_groups,
)

__all__ = ["STORED_SNIPPET_CHARS", "LanceDBVectorStore", "read_recorded_vector_dim", "record_vector_dim"]

# DataFusion expands every literal in a large ``IN`` filter into native query
# state. A several-thousand-path generation level can otherwise commit
# gigabytes before returning even though the selected result is small.
_SUMMARY_PATH_BATCH_SIZE = 100

logger = logging.getLogger(__name__)

# LanceDB caches each table version's manifest (default cap 1 GiB), and every
# manifest lists all fragments so far. Writes are batched below, but update runs
# and long-lived servers still accumulate versions; reads only need the latest.
_METADATA_CACHE_BYTES = 64 * 1024 * 1024

# Rows per LanceDB write in ``embed_batch``. Every write is a table version
# and a fragment, and ``merge_insert`` joins against the whole table, so
# writing each 16-item embedder chunk on its own made a level's writes grow
# with the square of its size (22k pages: about 1,400 versions). Rows are
# buffered as float32 Arrow data, about 8 KB each at 1,536 dimensions, so a
# full buffer stays near 32 MB.
_UPSERT_BATCH_ROWS = 4096

# A search returns pages, but a long page is stored as several rows, one per
# piece. The nearest rows are fetched with this much headroom so that a few
# long pages crowding the top cannot leave fewer than *limit* distinct pages;
# the fetch widens until it can.
_PAGE_OVERFETCH = 4

# ``STORED_SNIPPET_CHARS`` — how much of a page's content each row keeps — is
# defined with the embed recipe and re-exported here so the historical import
# path keeps working.
#
# A hit's evidence snippet should show the region the query matched, not the
# page's opening line: on a generated page that opener is the same
# ``## Overview`` paragraph every time. The full-text arm holds the whole page
# at search time and can cut a window from it; this arm cannot, because what a
# search sees was fixed when the row was written. So the row keeps enough
# content for a window to exist inside it. A row written before the widening
# holds 200 characters and simply windows to its opener.


def _dim_record(db_path: str | Path, table_name: str) -> Path:
    return Path(db_path) / f"{table_name}.vector_dim"


def read_recorded_vector_dim(db_path: str | Path, table_name: str) -> int | None:
    """The width last recorded for *table_name*, read without importing lancedb.

    Importing lancedb costs well over a second, and the store-upgrade check
    runs on every ``update``. ``None`` means "no usable record": no file, an
    unparsable one, or a record whose table directory is gone.
    """
    if not (Path(db_path) / f"{table_name}.lance").is_dir():
        return None
    try:
        dim = int(_dim_record(db_path, table_name).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return dim if dim > 0 else None


def record_vector_dim(db_path: str | Path, table_name: str, dim: int | None) -> None:
    """Record (or with ``None``, forget) *table_name*'s width. Best-effort."""
    path = _dim_record(db_path, table_name)
    try:
        if dim is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(str(dim), encoding="ascii")
    except OSError:
        pass


def _evidence(stored: str, query: str | None) -> str:
    """The served snippet, cut from the wider text a row keeps.

    With a query, a window centred on what it matched. Without one — the raw
    vector path, which carries no question — the opener, at the width it had
    before the stored column was widened.
    """
    if not stored:
        return ""
    if query:
        return snippet_around(stored, query)
    return stored[:_SNIPPET_LEN].rstrip()


def _paths_in_filter(paths: list[str]) -> str:
    """Build an SQL-injection-safe ``target_path IN (...)`` LanceDB filter.

    LanceDB's ``.where()`` takes a DataFusion SQL string with no bind
    parameters, so each path is quoted with the same quote-doubling escape
    the single-path lookup uses.
    """
    quoted = ", ".join("'" + p.replace("'", "''") + "'" for p in paths)
    return f"target_path IN ({quoted})"


def _page_ids_in_filter(page_ids: list[str]) -> str:
    """Build an SQL-injection-safe ``page_id IN (...)`` LanceDB filter.

    Mirrors :func:`_paths_in_filter` but on the ``page_id`` column, using the
    same quote-doubling escape as the single-id delete.
    """
    quoted = ", ".join("'" + p.replace("'", "''") + "'" for p in page_ids)
    return f"page_id IN ({quoted})"


def _last_row_per_page(batches: list[Any]) -> Any:
    """One Arrow table from *batches*, keeping each page_id's last write.

    ``merge_insert`` rejects a source holding a key twice once that key is
    in the table, and inserts both rows when it is not. Written chunk by
    chunk, a repeated page simply overwrote itself, so the last occurrence
    is the one that wins here too. A write is all of a page's pieces, which
    sit together starting at ``chunk_index`` 0, so the last write is every
    row from the page's last piece 0 on.
    """
    import pyarrow as pa  # type: ignore[import]

    table = pa.Table.from_batches(batches)
    page_ids = table.column("page_id").to_pylist()
    chunk_indexes = table.column("chunk_index").to_pylist()
    last_write = {
        page_id: i
        for i, (page_id, index) in enumerate(zip(page_ids, chunk_indexes, strict=True))
        if index == 0
    }
    keep = [i for i, page_id in enumerate(page_ids) if i >= last_write.get(page_id, 0)]
    if len(keep) < table.num_rows:
        table = table.take(keep)
    return table


class LanceDBUnavailableError(RuntimeError):
    """lancedb is missing or broken: an install problem, not a store one."""


def store_open_fix_hint(exc: BaseException) -> str:
    """What to do about a store that would not open, by what actually failed.

    Only an install problem earns "reinstall"; a locked or unreadable table
    does not, and telling someone to reinstall for it sends them the wrong way.
    """
    if isinstance(exc, (LanceDBUnavailableError, ImportError, AttributeError)):
        return "pip install --force-reinstall lancedb"
    return "retry; if it persists, run repowise doctor"


class LanceDBVectorStore(VectorStore):
    """Vector store backed by LanceDB (embedded, local file storage).

    Requires the ``repowise-core[search]`` extra:
        pip install repowise-core[search]

    Data is stored in *db_path* (e.g. ``.repowise/lancedb/``).
    The LanceDB table is created lazily on the first call to
    :meth:`embed_and_upsert`.
    """

    persists_across_runs = True

    _TABLE_NAME = "wiki_pages"

    def __init__(self, db_path: str, embedder: Embedder, table_name: str | None = None) -> None:
        self._db_path = db_path
        self._embedder = embedder
        self._table_name = table_name or self._TABLE_NAME
        self._db = None
        self._table = None
        self._dim_recorded = False
        # Tables written before pages were chunked lack ``chunk_index`` and
        # ``chunk_count``. Reads cope; the first write adds the columns.
        self._chunked = False

    async def _ensure_connected(self) -> None:
        if self._db is not None:
            return
        try:
            import lancedb  # type: ignore[import]

            connect_async = lancedb.connect_async
        except (ImportError, AttributeError) as exc:
            # A partial install imports fine and lacks the async API, which
            # used to surface as a bare AttributeError far from its cause.
            raise LanceDBUnavailableError(
                f"LanceDB is missing or broken ({type(exc).__name__}: {exc}). "
                "Reinstall it with: pip install --force-reinstall lancedb"
            ) from exc

        # ``Session`` arrived after the oldest supported LanceDB; without it
        # the connection keeps the library default.
        session_cls = getattr(lancedb, "Session", None)
        kwargs = (
            {"session": session_cls(metadata_cache_size_bytes=_METADATA_CACHE_BYTES)}
            if session_cls is not None
            else {}
        )
        self._db = await connect_async(self._db_path, **kwargs)
        table_names = await self._db.table_names()
        if self._table_name in table_names:
            self._table = await self._db.open_table(self._table_name)
            self._chunked = "chunk_index" in (await self._table.schema()).names
        else:
            self._table = None  # will be created on first upsert

    @staticmethod
    def _existing_vector_dim(schema) -> int | None:
        """Return the fixed-length dimension of the ``vector`` field, or None.

        Returns None when the field is absent or not a fixed-size list (in
        which case we can't meaningfully compare dimensions).
        """
        try:
            field = schema.field("vector")
        except KeyError:
            return None
        list_size = getattr(field.type, "list_size", None)
        # pyarrow uses ``list_size == -1`` for variable-length lists.
        if isinstance(list_size, int) and list_size > 0:
            return list_size
        return None

    async def _ensure_table(self, sample_vector: list[float]) -> None:
        """Create the LanceDB table if it does not exist yet.

        If a table already exists but its vector dimension differs from the
        current embedder's output (e.g. the embedder was switched from
        ``mock`` (dim 8) to ``openai`` (dim 1536) between reindexes), the stale
        table is dropped and recreated. Otherwise every write would fail deep
        inside LanceDB with an opaque IO error that never mentions dimensions.
        """
        try:
            import pyarrow as pa  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "pyarrow is required for LanceDBVectorStore. "
                "It is installed automatically with lancedb."
            ) from exc

        dim = len(sample_vector)

        if self._table is not None:
            existing_dim = self._existing_vector_dim(await self._table.schema())
            if existing_dim is None or existing_dim == dim:
                # Re-stamped once per store so a missing or stale record heals
                # on the next write instead of lingering.
                if existing_dim is not None and not self._dim_recorded:
                    record_vector_dim(self._db_path, self._table_name, existing_dim)
                    self._dim_recorded = True
                if not self._chunked:
                    # Every row of an older table is one whole-page vector.
                    await self._table.add_columns(
                        {"chunk_index": "CAST(0 AS INT)", "chunk_count": "CAST(1 AS INT)"}
                    )
                    self._chunked = True
                return
            # Embedder changed dimensions — the old vectors are unusable.
            # Forget the record first so a crash mid-rebuild leaves none.
            record_vector_dim(self._db_path, self._table_name, None)
            await self._db.drop_table(self._table_name)  # type: ignore[union-attr]
            self._table = None

        schema = pa.schema(
            [
                pa.field("page_id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
                pa.field("title", pa.string()),
                pa.field("page_type", pa.string()),
                pa.field("target_path", pa.string()),
                pa.field("content_snippet", pa.string()),
                pa.field("chunk_index", pa.int32()),
                pa.field("chunk_count", pa.int32()),
            ]
        )
        self._table = await self._db.create_table(  # type: ignore[union-attr]
            self._table_name, schema=schema, exist_ok=True
        )
        self._chunked = True
        record_vector_dim(self._db_path, self._table_name, dim)
        self._dim_recorded = True

    @staticmethod
    def _row(
        page_id: str,
        vector: list[float],
        metadata: dict,
        *,
        chunk_index: int = 0,
        chunk_count: int = 1,
    ) -> dict:
        content = str(metadata.get("content", ""))
        return {
            "page_id": page_id,
            "vector": [float(v) for v in vector],
            "title": str(metadata.get("title", "")),
            "page_type": str(metadata.get("page_type", "")),
            "target_path": str(metadata.get("target_path", "")),
            "content_snippet": content[:STORED_SNIPPET_CHARS],
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
        }

    @classmethod
    def _page_rows(cls, page: PageChunks, vectors: list[list[float]]) -> list[dict]:
        """One row per piece. A later piece keeps its own text as evidence,
        so a hit on the tail of a page shows the tail rather than the opener."""
        (page_id, text, metadata), pieces = page
        return [
            cls._row(
                page_id,
                vector,
                {"content": text, **metadata} if i == 0 else {**metadata, "content": piece},
                chunk_index=i,
                chunk_count=len(pieces),
            )
            for i, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
        ]

    async def _upsert_rows(self, rows: list[dict] | Any) -> None:
        """Replace the pages in *rows* (row dicts or an Arrow table) in one commit.

        Rows upsert on ``(page_id, chunk_index)``. A page now shorter than when
        it was last written would keep its surplus pieces, so the same merge
        deletes them: a page is never left holding pieces of two writes, and a
        write that fails leaves the previous one whole.
        """
        page_ids = (
            {str(row["page_id"]) for row in rows}
            if isinstance(rows, list)
            else set(rows.column("page_id").to_pylist())
        )
        try:
            builder = (
                self._table.merge_insert(["page_id", "chunk_index"])  # type: ignore[union-attr]
                .when_matched_update_all()
                .when_not_matched_insert_all()
            )
            # Only pages already holding more than one piece can have a surplus.
            # Asking first keeps the delete filter to those few pages rather
            # than every page in a several-thousand-row write.
            chunked = await self._chunked_page_ids()
            shrinkable = sorted(page_ids & chunked)
            if shrinkable:
                builder = builder.when_not_matched_by_source_delete(
                    f"chunk_index > 0 AND {_page_ids_in_filter(shrinkable)}"
                )
            await builder.execute(rows)
        except AttributeError:
            # Fallback for older LanceDB versions: delete + add
            if not isinstance(rows, list):
                rows = rows.to_pylist()
            ordered = sorted(page_ids)
            for i in range(0, len(ordered), 500):
                await self._table.delete(_page_ids_in_filter(ordered[i : i + 500]))  # type: ignore[union-attr]
            await self._table.add(rows)  # type: ignore[union-attr]

    async def _chunked_page_ids(self) -> set[str]:
        """Pages stored as more than one piece."""
        rows = (
            await self._table.query()  # type: ignore[union-attr]
            .where("chunk_index > 0")
            .select(["page_id"])
            .to_list()
        )
        return {r["page_id"] for r in rows}

    async def embed_and_upsert(self, page_id: str, text: str, metadata: dict) -> None:
        await self._ensure_connected()
        for group in iter_page_chunk_groups([(page_id, text, metadata)]):
            vectors = await embed_page_chunks(self._embedder, group)
            await self._ensure_table(vectors[0][0])
            await self._upsert_rows(self._page_rows(group[0], vectors[0]))

    async def embed_batch(self, items: list[tuple[str, str, dict]]) -> None:
        """Embed in request-sized chunks and write in a few large upserts.

        One embedder call per :data:`EMBED_BATCH_MAX_ITEMS` pieces — a whole
        generation level in a single request blew OpenAI's 300k-token cap
        and silently lost every file-page embedding. A page longer than one
        input is several pieces, embedded together and failing together. Writes are buffered up
        to :data:`_UPSERT_BATCH_ROWS` rows (see there for why). A failed
        chunk no longer sinks the rest; the summary error is raised at the
        end so callers still see the loss. A failed write reports every
        chunk it carried, at the ``persistence`` stage.
        """
        if not items:
            return
        await self._ensure_connected()
        failures: list[BatchChunkFailure] = []
        pending: list[Any] = []  # Arrow record batches, one per embedded chunk
        pending_chunks: list[list[tuple[str, str, dict]]] = []
        pending_rows = 0

        async def flush() -> None:
            nonlocal pending_rows
            if not pending:
                return
            try:
                await self._upsert_rows(_last_row_per_page(pending))
            except Exception as exc:  # preserve vectors' failure stage for callers
                failures.extend(
                    BatchChunkFailure(tuple(chunk), "persistence", exc) for chunk in pending_chunks
                )
            pending.clear()
            pending_chunks.clear()
            pending_rows = 0

        for group in iter_page_chunk_groups(items):
            chunk = [item for item, _pieces in group]
            try:
                vectors = await embed_page_chunks(self._embedder, group)
            except Exception as exc:  # isolate per provider request
                failures.append(BatchChunkFailure(tuple(chunk), "embedding", exc))
                continue
            try:
                await self._ensure_table(vectors[0][0])
                rows = [
                    row
                    for page, page_vectors in zip(group, vectors, strict=True)
                    for row in self._page_rows(page, page_vectors)
                ]
                pending.append(await self._record_batch(rows))
            except Exception as exc:  # preserve vectors' failure stage for callers
                failures.append(BatchChunkFailure(tuple(chunk), "persistence", exc))
                continue
            pending_chunks.append(chunk)
            pending_rows += len(rows)
            if pending_rows >= _UPSERT_BATCH_ROWS:
                await flush()
        await flush()
        if failures:
            raise BatchEmbeddingError(failures=failures, total_items=len(items))

    async def _record_batch(self, rows: list[dict]) -> Any:
        """*rows* as Arrow data in the table's schema (float32 vectors)."""
        import pyarrow as pa  # type: ignore[import]

        schema = await self._table.schema()  # type: ignore[union-attr]
        return pa.RecordBatch.from_pylist(rows, schema=schema)

    async def _search_by_vector(
        self, q_vec: list[float], limit: int, query: str | None = None
    ) -> list[SearchResult]:
        # Query with explicit cosine distance so ``_distance`` is a cosine
        # distance (1 - cos); we return ``1 - _distance`` = cosine similarity.
        # This makes the score semantics match the other backends
        # (InMemory/pgvector both return cosine similarity, higher = better),
        # so callers can apply a single similarity threshold uniformly.
        #
        # A page scores as its best piece: rows come back nearest first, so
        # the first row seen for a page is its best.
        fetch = limit * _PAGE_OVERFETCH
        while True:
            query_builder = self._table.query().nearest_to(q_vec)  # type: ignore[union-attr]
            if hasattr(query_builder, "distance_type"):
                query_builder = query_builder.distance_type("cosine")
            rows = await query_builder.limit(fetch).to_list()
            best: dict[str, dict] = {}
            for r in rows:
                best.setdefault(r["page_id"], r)
            if len(best) >= limit or len(rows) < fetch:
                break
            fetch *= 2
        raw = list(best.values())[:limit]

        # A caller that came in by raw vector has no query text to centre on,
        # so it gets the opener — at the width it always had. The stored
        # column is now wider than the snippet it serves, and returning it
        # whole would push ten times the text into a prompt.
        return [
            SearchResult(
                page_id=r["page_id"],
                title=r.get("title", ""),
                page_type=r.get("page_type", ""),
                target_path=r.get("target_path", ""),
                score=1.0 - float(r.get("_distance", 1.0)),
                snippet=_evidence(r.get("content_snippet", ""), query),
                search_type="vector",
            )
            for r in raw
        ]

    async def upsert_vectors(self, items: list[tuple[str, list[float], dict]]) -> bool:
        if not items:
            return True
        await self._ensure_connected()
        await self._ensure_table([float(v) for v in items[0][1]])
        # Last occurrence wins, as it did when rows merged on page_id alone.
        rows = list(
            {
                page_id: self._row(page_id, [float(v) for v in vector], metadata)
                for page_id, vector, metadata in items
            }.values()
        )
        await self._upsert_rows(rows)
        return True

    async def search(
        self, query: str, limit: int = 10, *, kind: str = "query"
    ) -> list[SearchResult]:
        await self._ensure_connected()
        if self._table is None:
            return []

        q_vecs = await self._embedder.embed([query], kind=kind)
        return await self._search_by_vector([float(v) for v in q_vecs[0]], limit, query=query)

    async def search_by_vector(self, vector: list[float], limit: int = 10) -> list[SearchResult]:
        await self._ensure_connected()
        if self._table is None:
            return []
        return await self._search_by_vector([float(v) for v in vector], limit)

    async def search_many(
        self, queries: list[str], limit: int = 10, *, kind: str = "query"
    ) -> list[list[SearchResult]]:
        """One embedder call for all queries; the vector lookups are local."""
        if not queries:
            return []
        await self._ensure_connected()
        if self._table is None:
            return [[] for _ in queries]
        q_vecs = await self._embedder.embed(list(queries), kind=kind)
        out: list[list[SearchResult]] = []
        for query, q_vec in zip(queries, q_vecs, strict=True):
            try:
                out.append(
                    await self._search_by_vector([float(v) for v in q_vec], limit, query=query)
                )
            except Exception:
                out.append([])
        return out

    async def delete(self, page_id: str) -> None:
        await self._ensure_connected()
        if self._table is not None:
            safe_id = page_id.replace("'", "''")
            await self._table.delete(f"page_id = '{safe_id}'")  # type: ignore[union-attr]

    async def delete_many(self, page_ids: list[str]) -> None:
        if not page_ids:
            return
        await self._ensure_connected()
        if self._table is None:
            return
        # LanceDB's ``.where()`` has no bind params, so build a quoted IN
        # predicate; chunk to keep the SQL string bounded.
        for i in range(0, len(page_ids), 500):
            batch = page_ids[i : i + 500]
            await self._table.delete(_page_ids_in_filter(batch))  # type: ignore[union-attr]

    async def close(self) -> None:
        self._table = None
        self._db = None

    def _first_pieces(self, predicate: str) -> str:
        """*predicate* narrowed to each page's opening piece, which holds its summary."""
        return f"({predicate}) AND chunk_index = 0" if self._chunked else predicate

    async def holds_current_vectors(self) -> bool:
        width = getattr(self._embedder, "dimensions", None)
        return width is not None and read_recorded_vector_dim(
            self._db_path, self._table_name
        ) == width

    async def list_page_ids(self) -> set[str]:
        await self._ensure_connected()
        if self._table is None:
            return set()

        try:
            rows = await self._table.query().select(["page_id"]).to_list()  # type: ignore[union-attr]
            return {r["page_id"] for r in rows}
        except Exception as exc:
            logger.warning(
                "Failed to read page IDs from LanceDB vector store at %s. "
                "The vector store may be damaged. Repowise will regenerate "
                "pages instead. Run 'repowise reindex' to rebuild the vector store. "
                "Error: %s",
                self._db_path,
                exc,
            )
            return set()

    async def vector_coverage(self) -> VectorCoverage | None:
        await self._ensure_connected()
        if self._table is None:
            return VectorCoverage(pages=0, chunked_pages=0, incomplete=frozenset())
        if not self._chunked:
            pages = await self.list_page_ids()
            return VectorCoverage(pages=len(pages), chunked_pages=0, incomplete=frozenset())
        rows = (
            await self._table.query()  # type: ignore[union-attr]
            .select(["page_id", "chunk_index", "chunk_count"])
            .to_list()
        )
        held: dict[str, set[int]] = {}
        planned: dict[str, set[int]] = {}
        for r in rows:
            held.setdefault(r["page_id"], set()).add(int(r["chunk_index"]))
            planned.setdefault(r["page_id"], set()).add(int(r["chunk_count"]))
        incomplete = frozenset(
            page_id
            for page_id, indexes in held.items()
            if len(planned[page_id]) != 1 or indexes != set(range(next(iter(planned[page_id]))))
        )
        return VectorCoverage(
            pages=len(held),
            chunked_pages=sum(1 for indexes in held.values() if len(indexes) > 1),
            incomplete=incomplete,
        )

    async def get_page_summary_by_path(self, path: str) -> dict | None:
        """Return {'summary': str, 'key_exports': list[str]} for a previously-indexed page, or None.

        The summary is the opening of 'content_snippet'. That column holds more
        than this now, because a search cuts an evidence window out of it, but
        this text goes into a prompt — so it keeps the width it always had
        rather than growing with the store behind it. 'key_exports' is not in
        the schema, so it comes back empty; the caller only uses the summary.
        """
        await self._ensure_connected()
        if self._table is None:
            return None

        safe_path = path.replace("'", "''")
        try:
            rows = (
                await self._table.query()  # type: ignore[union-attr]
                .where(self._first_pieces(f"target_path = '{safe_path}'"))
                .select(["content_snippet"])
                .limit(1)
                .to_list()
            )
        except Exception:
            return None

        if not rows:
            return None

        summary = str(rows[0].get("content_snippet") or "")[:_SNIPPET_LEN]
        return {"summary": summary, "key_exports": []}

    async def get_page_summaries_by_paths(self, paths: list[str]) -> dict[str, dict]:
        """Read summaries with bounded ``IN``-filtered scans.

        Mirrors the single-path semantics (first row per path wins, empty
        summaries dropped, ``key_exports`` not stored in this schema). Batches
        bound DataFusion's native query-plan allocation for large levels.
        """
        if not paths:
            return {}
        await self._ensure_connected()
        if self._table is None:
            return {}

        out: dict[str, dict] = {}
        unique_paths = list(dict.fromkeys(paths))
        for index in range(0, len(unique_paths), _SUMMARY_PATH_BATCH_SIZE):
            batch = unique_paths[index : index + _SUMMARY_PATH_BATCH_SIZE]
            try:
                rows = (
                    await self._table.query()  # type: ignore[union-attr]
                    .where(self._first_pieces(_paths_in_filter(batch)))
                    .select(["target_path", "content_snippet"])
                    .to_list()
                )
            except Exception:
                return {}

            for row in rows:
                target_path = str(row.get("target_path") or "")
                if not target_path or target_path in out:
                    continue
                summary = str(row.get("content_snippet") or "")[:_SNIPPET_LEN]
                if summary:
                    out[target_path] = {"summary": summary, "key_exports": []}
        return out
