"""PostgreSQL/pgvector-backed vector store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.providers.embedding.base import Embedder

from ..search import SearchResult
from ._base import VectorStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = ["PgVectorStore"]


def _encode(vector: list[float]) -> str:
    """Encode a vector as the pgvector literal ``"[0.1,0.2,...]"``."""
    return "[" + ",".join(str(v) for v in vector) + "]"


def _summary_payload(content: object, metadata: object) -> dict:
    """Build the ``{'summary', 'key_exports'}`` payload from a wiki_pages row."""
    key_exports: list[str] = []
    if metadata and isinstance(metadata, dict):
        key_exports = list(metadata.get("exports", []))
    elif metadata and isinstance(metadata, str):
        import json

        try:
            meta = json.loads(metadata)
            key_exports = list(meta.get("exports", []))
        except (json.JSONDecodeError, AttributeError):
            pass

    return {"summary": str(content or "")[:500], "key_exports": key_exports}


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
        vec_str = _encode(vectors[0])

        from sqlalchemy.sql import text as sa_text

        async with self._session_factory() as session:
            await session.execute(
                sa_text("UPDATE wiki_pages SET embedding = CAST(:emb AS vector) WHERE id = :pid"),
                {"emb": vec_str, "pid": page_id},
            )
            await session.commit()

    async def embed_batch(self, items: list[tuple[str, str, dict]]) -> None:
        if not items:
            return
        texts = [text for _, text, _ in items]
        vectors = await self._embedder.embed(texts)
        params = [
            {"emb": _encode(vector), "pid": page_id}
            for (page_id, _text, _meta), vector in zip(items, vectors, strict=True)
        ]

        from sqlalchemy.sql import text as sa_text

        stmt = sa_text("UPDATE wiki_pages SET embedding = CAST(:emb AS vector) WHERE id = :pid")
        async with self._session_factory() as session:
            # executemany: one driver round-trip batch instead of one UPDATE
            # round-trip per row.
            await session.execute(stmt, params)
            await session.commit()

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        q_vecs = await self._embedder.embed([query])
        vec_str = _encode(q_vecs[0])

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

    async def search_many(self, queries: list[str], limit: int = 10) -> list[list[SearchResult]]:
        """One embedder call for all queries; per-query SELECTs share a session."""
        if not queries:
            return []
        q_vecs = await self._embedder.embed(list(queries))

        from sqlalchemy.sql import text as sa_text

        stmt = sa_text(
            "SELECT id, title, content, page_type, target_path, "
            "  1 - (embedding <=> CAST(:q AS vector)) AS score "
            "FROM wiki_pages "
            "WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> CAST(:q AS vector) "
            "LIMIT :lim"
        )
        out: list[list[SearchResult]] = []
        async with self._session_factory() as session:
            for q_vec in q_vecs:
                try:
                    rows = await session.execute(stmt, {"q": _encode(q_vec), "lim": limit})
                    raw = rows.fetchall()
                except Exception:
                    out.append([])
                    continue
                out.append(
                    [
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
                )
        return out

    async def delete(self, page_id: str) -> None:
        from sqlalchemy.sql import text as sa_text

        async with self._session_factory() as session:
            await session.execute(
                sa_text("UPDATE wiki_pages SET embedding = NULL WHERE id = :pid"),
                {"pid": page_id},
            )
            await session.commit()

    async def delete_many(self, page_ids: list[str]) -> None:
        if not page_ids:
            return
        from sqlalchemy import bindparam
        from sqlalchemy.sql import text as sa_text

        stmt = sa_text("UPDATE wiki_pages SET embedding = NULL WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )

        async with self._session_factory() as session:
            await session.execute(stmt, {"ids": list(page_ids)})
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

        Reads the 'content' column (first 500 chars) from the wiki_pages table
        matched by target_path. 'key_exports' is derived from the page's
        ``exports`` if stored in a metadata JSON column; otherwise returns [].
        """
        from sqlalchemy.sql import text as sa_text

        async with self._session_factory() as session:
            rows = await session.execute(
                sa_text(
                    "SELECT content, metadata FROM wiki_pages WHERE target_path = :path LIMIT 1"
                ),
                {"path": path},
            )
            row = rows.fetchone()

        if row is None:
            return None

        return _summary_payload(row[0], row[1])

    async def get_page_summaries_by_paths(self, paths: list[str]) -> dict[str, dict]:
        """One ``IN``-filtered SELECT instead of one query per path.

        Like the single-path variant (``LIMIT 1`` with no ``ORDER BY``), when
        several pages share a ``target_path`` an arbitrary one wins — here the
        first row returned per path.
        """
        if not paths:
            return {}

        from sqlalchemy import bindparam
        from sqlalchemy.sql import text as sa_text

        stmt = sa_text(
            "SELECT target_path, content, metadata FROM wiki_pages WHERE target_path IN :paths"
        ).bindparams(bindparam("paths", expanding=True))

        async with self._session_factory() as session:
            rows = await session.execute(stmt, {"paths": list(paths)})
            raw = rows.fetchall()

        out: dict[str, dict] = {}
        for r in raw:
            tp = str(r[0])
            if tp in out:
                continue
            payload = _summary_payload(r[1], r[2])
            if payload.get("summary"):
                out[tp] = payload
        return out
