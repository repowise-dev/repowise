"""LanceDB-backed vector store (embedded, local file storage)."""

from __future__ import annotations

from repowise.core.providers.embedding.base import Embedder

from ..search import SearchResult
from ._base import VectorStore

__all__ = ["LanceDBVectorStore"]


def _paths_in_filter(paths: list[str]) -> str:
    """Build an SQL-injection-safe ``target_path IN (...)`` LanceDB filter.

    LanceDB's ``.where()`` takes a DataFusion SQL string with no bind
    parameters, so each path is quoted with the same quote-doubling escape
    the single-path lookup uses.
    """
    quoted = ", ".join("'" + p.replace("'", "''") + "'" for p in paths)
    return f"target_path IN ({quoted})"


class LanceDBVectorStore(VectorStore):
    """Vector store backed by LanceDB (embedded, local file storage).

    Requires the ``repowise-core[search]`` extra:
        pip install repowise-core[search]

    Data is stored in *db_path* (e.g. ``.repowise/lancedb/``).
    The LanceDB table is created lazily on the first call to
    :meth:`embed_and_upsert`.
    """

    _TABLE_NAME = "wiki_pages"

    def __init__(self, db_path: str, embedder: Embedder, table_name: str | None = None) -> None:
        self._db_path = db_path
        self._embedder = embedder
        self._table_name = table_name or self._TABLE_NAME
        self._db = None
        self._table = None

    async def _ensure_connected(self) -> None:
        if self._db is not None:
            return
        try:
            import lancedb  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "LanceDB is not installed. Install it with: pip install repowise-core[search]"
            ) from exc

        self._db = await lancedb.connect_async(self._db_path)
        table_names = await self._db.table_names()
        if self._table_name in table_names:
            self._table = await self._db.open_table(self._table_name)
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
                return
            # Embedder changed dimensions — the old vectors are unusable.
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
            ]
        )
        self._table = await self._db.create_table(  # type: ignore[union-attr]
            self._table_name, schema=schema, exist_ok=True
        )

    @staticmethod
    def _row(page_id: str, vector: list[float], metadata: dict) -> dict:
        content = str(metadata.get("content", ""))
        return {
            "page_id": page_id,
            "vector": [float(v) for v in vector],
            "title": str(metadata.get("title", "")),
            "page_type": str(metadata.get("page_type", "")),
            "target_path": str(metadata.get("target_path", "")),
            "content_snippet": content[:200],
        }

    async def _upsert_rows(self, rows: list[dict]) -> None:
        # merge_insert: upsert by page_id (LanceDB 0.12+)
        try:
            await (
                self._table.merge_insert("page_id")  # type: ignore[union-attr]
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(rows)
            )
        except AttributeError:
            # Fallback for older LanceDB versions: delete + add
            for row in rows:
                safe_id = str(row["page_id"]).replace("'", "''")
                await self._table.delete(f"page_id = '{safe_id}'")  # type: ignore[union-attr]
            await self._table.add(rows)  # type: ignore[union-attr]

    async def embed_and_upsert(self, page_id: str, text: str, metadata: dict) -> None:
        await self._ensure_connected()
        vectors = await self._embedder.embed([text])
        vector = vectors[0]
        await self._ensure_table(vector)
        meta = {"content": text, **metadata}
        await self._upsert_rows([self._row(page_id, vector, meta)])

    async def embed_batch(self, items: list[tuple[str, str, dict]]) -> None:
        if not items:
            return
        await self._ensure_connected()
        texts = [text for _, text, _ in items]
        vectors = await self._embedder.embed(texts)
        await self._ensure_table(vectors[0])
        rows = [
            self._row(page_id, vector, {"content": text, **metadata})
            for (page_id, text, metadata), vector in zip(items, vectors, strict=True)
        ]
        await self._upsert_rows(rows)

    async def _search_by_vector(self, q_vec: list[float], limit: int) -> list[SearchResult]:
        # Query with explicit cosine distance so ``_distance`` is a cosine
        # distance (1 - cos); we return ``1 - _distance`` = cosine similarity.
        # This makes the score semantics match the other backends
        # (InMemory/pgvector both return cosine similarity, higher = better),
        # so callers can apply a single similarity threshold uniformly.
        query_builder = self._table.query().nearest_to(q_vec)  # type: ignore[union-attr]
        if hasattr(query_builder, "distance_type"):
            query_builder = query_builder.distance_type("cosine")
        raw = await query_builder.limit(limit).to_list()

        return [
            SearchResult(
                page_id=r["page_id"],
                title=r.get("title", ""),
                page_type=r.get("page_type", ""),
                target_path=r.get("target_path", ""),
                score=1.0 - float(r.get("_distance", 1.0)),
                snippet=r.get("content_snippet", ""),
                search_type="vector",
            )
            for r in raw
        ]

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        await self._ensure_connected()
        if self._table is None:
            return []

        q_vecs = await self._embedder.embed([query])
        return await self._search_by_vector([float(v) for v in q_vecs[0]], limit)

    async def search_many(
        self, queries: list[str], limit: int = 10
    ) -> list[list[SearchResult]]:
        """One embedder call for all queries; the vector lookups are local."""
        if not queries:
            return []
        await self._ensure_connected()
        if self._table is None:
            return [[] for _ in queries]
        q_vecs = await self._embedder.embed(list(queries))
        out: list[list[SearchResult]] = []
        for q_vec in q_vecs:
            try:
                out.append(await self._search_by_vector([float(v) for v in q_vec], limit))
            except Exception:
                out.append([])
        return out

    async def delete(self, page_id: str) -> None:
        await self._ensure_connected()
        if self._table is not None:
            safe_id = page_id.replace("'", "''")
            await self._table.delete(f"page_id = '{safe_id}'")  # type: ignore[union-attr]

    async def close(self) -> None:
        self._table = None
        self._db = None

    async def list_page_ids(self) -> set[str]:
        await self._ensure_connected()
        if self._table is None:
            return set()
        rows = await self._table.query().select(["page_id"]).to_list()  # type: ignore[union-attr]
        return {r["page_id"] for r in rows}

    async def get_page_summary_by_path(self, path: str) -> dict | None:
        """Return {'summary': str, 'key_exports': list[str]} for a previously-indexed page, or None.

        LanceDB stores up to 200 chars of content in 'content_snippet'; we use
        that as the summary. 'key_exports' is not stored in the schema, so we
        return [] — the caller only uses the text summary for prompt injection.
        """
        await self._ensure_connected()
        if self._table is None:
            return None

        safe_path = path.replace("'", "''")
        try:
            rows = (
                await self._table.query()  # type: ignore[union-attr]
                .where(f"target_path = '{safe_path}'")
                .select(["content_snippet"])
                .limit(1)
                .to_list()
            )
        except Exception:
            return None

        if not rows:
            return None

        summary = rows[0].get("content_snippet") or ""
        return {"summary": str(summary), "key_exports": []}

    async def get_page_summaries_by_paths(self, paths: list[str]) -> dict[str, dict]:
        """One ``IN``-filtered scan instead of one filtered query per path.

        Mirrors the single-path semantics (first row per path wins, empty
        summaries dropped, ``key_exports`` not stored in this schema).
        """
        if not paths:
            return {}
        await self._ensure_connected()
        if self._table is None:
            return {}

        try:
            rows = (
                await self._table.query()  # type: ignore[union-attr]
                .where(_paths_in_filter(paths))
                .select(["target_path", "content_snippet"])
                .to_list()
            )
        except Exception:
            return {}

        out: dict[str, dict] = {}
        for r in rows:
            tp = str(r.get("target_path") or "")
            if not tp or tp in out:
                continue
            summary = r.get("content_snippet") or ""
            if summary:
                out[tp] = {"summary": str(summary), "key_exports": []}
        return out
