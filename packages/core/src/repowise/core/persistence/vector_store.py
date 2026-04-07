"""Vector store abstraction and implementations for repowise semantic search.

Three implementations are provided:

InMemoryVectorStore
    Pure Python, no external dependencies.  Cosine similarity search over
    an in-memory dict.  Suitable for tests and development.

LanceDBVectorStore
    Embedded vector database stored in a local directory.  Requires the
    ``repowise-core[search]`` extra (lancedb>=0.12).

PgVectorStore
    Stores embeddings in the ``wiki_pages.embedding`` pgvector column.
    Requires the ``repowise-core[pgvector]`` extra and a running PostgreSQL
    with the ``vector`` extension enabled.

All stores accept an :class:`Embedder` at construction time and handle
embedding internally — callers pass raw text, not pre-computed vectors.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from repowise.core.providers.embedding.base import Embedder

from .search import SearchResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = [
    "InMemoryVectorStore",
    "LanceDBVectorStore",
    "PgVectorStore",
    "VectorStore",
]


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class VectorStore(ABC):
    """Abstract vector store.  All methods are async."""

    @abstractmethod
    async def embed_and_upsert(self, page_id: str, text: str, metadata: dict) -> None:
        """Embed *text* and upsert the vector under *page_id*."""
        ...

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Embed *query* and return the *limit* nearest pages."""
        ...

    @abstractmethod
    async def delete(self, page_id: str) -> None:
        """Remove the vector for *page_id* from the store."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by the store."""
        ...

    async def list_page_ids(self) -> set[str]:
        """Return the set of page IDs currently stored.

        Used by ``repowise doctor --repair`` to detect three-store
        inconsistencies.  Implementations may override for efficiency.
        """
        return set()  # default: empty (subclasses should override)

    async def get_page_summary_by_path(self, path: str) -> dict | None:
        """Return {'summary': str, 'key_exports': list[str]} for a previously-indexed page, or None.

        Used for RAG context injection during doc generation: when generating page B
        that imports A, we fetch A's previously-generated summary and feed it to the LLM.
        """
        return None  # default: no-op (subclasses should override)


# ---------------------------------------------------------------------------
# InMemoryVectorStore
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (returns 0.0 for zero vectors)."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    denom = norm_a * norm_b
    return dot / denom if denom > 0 else 0.0


class InMemoryVectorStore(VectorStore):
    """Cosine-similarity vector store backed by a plain Python dict.

    Suitable for unit tests and small-scale development use.
    No external dependencies beyond the Embedder.
    """

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        # page_id → (vector, metadata)
        self._store: dict[str, tuple[list[float], dict]] = {}

    async def embed_and_upsert(self, page_id: str, text: str, metadata: dict) -> None:
        vectors = await self._embedder.embed([text])
        self._store[page_id] = (vectors[0], dict(metadata))

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not self._store:
            return []
        q_vecs = await self._embedder.embed([query])
        q_vec = q_vecs[0]

        scored: list[tuple[float, str, dict]] = []
        for pid, (vec, meta) in self._store.items():
            score = _cosine(q_vec, vec)
            scored.append((score, pid, meta))

        scored.sort(key=lambda t: t[0], reverse=True)

        results = []
        for score, pid, meta in scored[:limit]:
            content = meta.get("content", "")
            results.append(
                SearchResult(
                    page_id=pid,
                    title=str(meta.get("title", "")),
                    page_type=str(meta.get("page_type", "")),
                    target_path=str(meta.get("target_path", "")),
                    score=score,
                    snippet=str(content)[:200].rstrip(),
                    search_type="vector",
                )
            )
        return results

    async def delete(self, page_id: str) -> None:
        self._store.pop(page_id, None)

    async def close(self) -> None:
        self._store.clear()

    async def list_page_ids(self) -> set[str]:
        return set(self._store.keys())

    async def get_page_summary_by_path(self, path: str) -> dict | None:
        """Return {'summary': str, 'key_exports': list[str]} for a previously-indexed page, or None.

        Used for RAG context injection during doc generation: when generating page B
        that imports A, we fetch A's previously-generated summary and feed it to the LLM.

        Implementation note: reads 'summary' from metadata if present (set by the
        generation pipeline), else falls back to the first 500 chars of 'content'.
        'key_exports' reads the 'exports' metadata field if present, else [].
        """
        for _pid, (_, meta) in self._store.items():
            if meta.get("target_path") == path:
                summary = meta.get("summary") or str(meta.get("content", ""))[:500]
                key_exports = meta.get("exports") or []
                return {"summary": summary, "key_exports": list(key_exports)}
        return None

    def __len__(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# LanceDBVectorStore
# ---------------------------------------------------------------------------


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

    async def _ensure_table(self, sample_vector: list[float]) -> None:
        """Create the LanceDB table if it does not exist yet."""
        if self._table is not None:
            return

        try:
            import pyarrow as pa  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "pyarrow is required for LanceDBVectorStore. "
                "It is installed automatically with lancedb."
            ) from exc

        dim = len(sample_vector)
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

    async def embed_and_upsert(self, page_id: str, text: str, metadata: dict) -> None:
        await self._ensure_connected()
        vectors = await self._embedder.embed([text])
        vector = vectors[0]
        await self._ensure_table(vector)

        content = str(metadata.get("content", text))
        row = {
            "page_id": page_id,
            "vector": [float(v) for v in vector],
            "title": str(metadata.get("title", "")),
            "page_type": str(metadata.get("page_type", "")),
            "target_path": str(metadata.get("target_path", "")),
            "content_snippet": content[:200],
        }

        # merge_insert: upsert by page_id (LanceDB 0.12+)
        try:
            await (
                self._table.merge_insert("page_id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute([row])
            )  # type: ignore[union-attr]
        except AttributeError:
            # Fallback for older LanceDB versions: delete + add
            safe_id = page_id.replace("'", "''")
            await self._table.delete(f"page_id = '{safe_id}'")  # type: ignore[union-attr]
            await self._table.add([row])  # type: ignore[union-attr]

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        await self._ensure_connected()
        if self._table is None:
            return []

        q_vecs = await self._embedder.embed([query])
        q_vec = [float(v) for v in q_vecs[0]]

        raw = (
            await self._table.query()  # type: ignore[union-attr]
            .nearest_to(q_vec)
            .limit(limit)
            .to_list()
        )

        return [
            SearchResult(
                page_id=r["page_id"],
                title=r.get("title", ""),
                page_type=r.get("page_type", ""),
                target_path=r.get("target_path", ""),
                score=float(r.get("_distance", 0.0)),
                snippet=r.get("content_snippet", ""),
                search_type="vector",
            )
            for r in raw
        ]

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

        Used for RAG context injection during doc generation: when generating page B
        that imports A, we fetch A's previously-generated summary and feed it to the LLM.

        Implementation note: LanceDB stores up to 200 chars of content in 'content_snippet'.
        We use that as the summary. 'key_exports' is not stored in the LanceDB schema, so
        we return [] — the caller only uses the text summary for prompt injection.
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


# ---------------------------------------------------------------------------
# PgVectorStore
# ---------------------------------------------------------------------------


class PgVectorStore(VectorStore):
    """Vector store that writes embeddings to the ``wiki_pages.embedding`` column.

    Requires:
    - PostgreSQL with the ``vector`` extension.
    - The Alembic migration ``0001_initial_schema`` has been applied.
    - The ``repowise-core[pgvector]`` extra.

    Uses raw SQL to avoid importing ``pgvector.sqlalchemy.Vector`` at module
    level (keeps the base package installable without the extra).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: Embedder,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder

    async def embed_and_upsert(self, page_id: str, text: str, metadata: dict) -> None:
        vectors = await self._embedder.embed([text])
        vector = vectors[0]
        # pgvector expects a list encoded as a string like "[0.1, 0.2, ...]"
        vec_str = "[" + ",".join(str(v) for v in vector) + "]"

        from sqlalchemy.sql import text as sa_text

        async with self._session_factory() as session:
            await session.execute(
                sa_text("UPDATE wiki_pages SET embedding = CAST(:emb AS vector) WHERE id = :pid"),
                {"emb": vec_str, "pid": page_id},
            )
            await session.commit()

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        q_vecs = await self._embedder.embed([query])
        q_vec = q_vecs[0]
        vec_str = "[" + ",".join(str(v) for v in q_vec) + "]"

        from sqlalchemy.sql import text as sa_text

        async with self._session_factory() as session:
            rows = await session.execute(
                sa_text(
                    "SELECT id, title, content, page_type, target_path, "
                    "  1 - (embedding <=> CAST(:q AS vector)) AS score "
                    "FROM wiki_pages "
                    "WHERE embedding IS NOT NULL "
                    "ORDER BY embedding <=> CAST(:q AS vector) "
                    "LIMIT :lim"
                ),
                {"q": vec_str, "lim": limit},
            )
            raw = rows.fetchall()

        return [
            SearchResult(
                page_id=r[0],
                title=r[1],
                page_type=r[3],
                target_path=r[4],
                score=float(r[5]),
                snippet=str(r[2])[:200].rstrip(),
                search_type="vector",
            )
            for r in raw
        ]

    async def delete(self, page_id: str) -> None:
        from sqlalchemy.sql import text as sa_text

        async with self._session_factory() as session:
            await session.execute(
                sa_text("UPDATE wiki_pages SET embedding = NULL WHERE id = :pid"),
                {"pid": page_id},
            )
            await session.commit()

    async def close(self) -> None:
        pass  # session_factory manages connection lifecycle

    async def list_page_ids(self) -> set[str]:
        from sqlalchemy.sql import text as sa_text

        async with self._session_factory() as session:
            rows = await session.execute(
                sa_text("SELECT id FROM wiki_pages WHERE embedding IS NOT NULL")
            )
            return {r[0] for r in rows.fetchall()}

    async def get_page_summary_by_path(self, path: str) -> dict | None:
        """Return {'summary': str, 'key_exports': list[str]} for a previously-indexed page, or None.

        Used for RAG context injection during doc generation: when generating page B
        that imports A, we fetch A's previously-generated summary and feed it to the LLM.

        Implementation note: reads the 'content' column (first 500 chars) from the
        wiki_pages table matched by target_path. 'key_exports' is derived from the
        page's exports if stored in a metadata JSON column; otherwise returns [].
        """
        from sqlalchemy.sql import text as sa_text

        async with self._session_factory() as session:
            rows = await session.execute(
                sa_text(
                    "SELECT content, metadata FROM wiki_pages "
                    "WHERE target_path = :path "
                    "LIMIT 1"
                ),
                {"path": path},
            )
            row = rows.fetchone()

        if row is None:
            return None

        content = str(row[0] or "")[:500]
        # Extract key_exports from metadata JSON column if present
        key_exports: list[str] = []
        if row[1] and isinstance(row[1], dict):
            key_exports = list(row[1].get("exports", []))
        elif row[1] and isinstance(row[1], str):
            import json
            try:
                meta = json.loads(row[1])
                key_exports = list(meta.get("exports", []))
            except (json.JSONDecodeError, AttributeError):
                pass

        return {"summary": content, "key_exports": key_exports}
