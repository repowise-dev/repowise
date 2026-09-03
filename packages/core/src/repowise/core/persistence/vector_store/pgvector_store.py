"""PostgreSQL/pgvector-backed vector store."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from repowise.core.providers.embedding.base import Embedder

from ..search import SearchResult
from ._base import VectorStore, iter_embed_chunks

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = ["PgVectorStore"]

logger = logging.getLogger(__name__)

_VECTOR_DIM_RE = re.compile(r"vector\s*\(\s*(\d+)\s*\)", re.IGNORECASE)


def _encode(vector: list[float]) -> str:
    """Encode a vector as the pgvector literal ``"[0.1,0.2,...]"``."""
    return "[" + ",".join(str(v) for v in vector) + "]"


def _is_dimension_mismatch_error(exc: Exception) -> bool:
    """True if *exc* is a Postgres dimension-mismatch error."""
    msg = str(exc).lower()
    return "dimension" in msg and ("expected" in msg or "mismatch" in msg or "vector" in msg)


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

    Dimension handling mirrors ``LanceDBVectorStore._ensure_table``: if the
    embedder changes dimensions (e.g. MockEmbedder 8-dim → OpenAI 1536-dim or
    Gemini 768-dim), the old vectors are unusable. LanceDB drops and recreates
    the table; pgvector alters the column type after clearing stale embeddings.
    """

    persists_across_runs = True

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: Embedder,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder

    async def _current_vector_dim(self) -> int | None:
        """Return the current ``wiki_pages.embedding`` dimension, or ``None``.

        Queries ``pg_attribute`` for the column's type. Returns ``None`` when
        the column is absent, is a generic ``vector`` without a fixed size, or
        the query fails (e.g. SQLite in tests).
        """
        from sqlalchemy.sql import text as sa_text

        try:
            async with self._session_factory() as session:
                row = await session.execute(
                    sa_text(
                        "SELECT format_type(atttypid, atttypmod) "
                        "FROM pg_attribute "
                        "WHERE attrelid = 'wiki_pages'::regclass "
                        "AND attname = 'embedding'"
                    )
                )
                val = row.scalar()
                if not val:
                    return None
                m = _VECTOR_DIM_RE.search(str(val))
                if m:
                    return int(m.group(1))
                # Generic vector without dimension — typmod -1
                return None
        except Exception:
            return None

    async def _migrate_vector_dim(self, new_dim: int) -> None:
        """Clear stale embeddings and alter the column to *new_dim*.

        Mirrors LanceDB's drop-and-recreate: old vectors at the wrong
        dimensionality are unusable, so they are cleared first. The alter is
        best-effort — if it fails (e.g. SQLite, missing extension), the
        subsequent write will surface the error.
        """
        from sqlalchemy.sql import text as sa_text

        try:
            async with self._session_factory() as session:
                await session.execute(sa_text("UPDATE wiki_pages SET embedding = NULL"))
                await session.commit()
        except Exception as exc:
            logger.warning("pgvector.clear_embeddings_failed", error=str(exc))
            return

        try:
            async with self._session_factory() as session:
                # After clearing, the column holds only NULLs, so the USING
                # clause can safely cast to the new dimension.
                await session.execute(
                    sa_text(
                        f"ALTER TABLE wiki_pages ALTER COLUMN embedding "
                        f"TYPE vector({new_dim}) USING NULL::vector({new_dim})"
                    )
                )
                await session.commit()
                logger.warning(
                    "pgvector.dimension_migrated",
                    old_dim="unknown",
                    new_dim=new_dim,
                )
        except Exception as exc:
            logger.warning("pgvector.alter_dimension_failed", error=str(exc), new_dim=new_dim)

    async def _ensure_dimension(self, expected_dim: int) -> None:
        """Ensure the pgvector column matches *expected_dim*, migrating if needed."""
        current = await self._current_vector_dim()
        if current is not None and current != expected_dim:
            logger.warning(
                "pgvector.dimension_mismatch",
                current_dim=current,
                expected_dim=expected_dim,
            )
            await self._migrate_vector_dim(expected_dim)

    async def embed_and_upsert(self, page_id: str, text: str, metadata: dict) -> None:
        vectors = await self._embedder.embed([text])
        vec_str = _encode(vectors[0])
        await self._ensure_dimension(len(vectors[0]))

        from sqlalchemy.sql import text as sa_text

        try:
            async with self._session_factory() as session:
                await session.execute(
                    sa_text(
                        "UPDATE wiki_pages SET embedding = CAST(:emb AS vector) WHERE id = :pid"
                    ),
                    {"emb": vec_str, "pid": page_id},
                )
                await session.commit()
        except Exception as exc:
            if _is_dimension_mismatch_error(exc):
                await self._migrate_vector_dim(len(vectors[0]))
                # Retry once after migration
                async with self._session_factory() as session:
                    await session.execute(
                        sa_text(
                            "UPDATE wiki_pages SET embedding = CAST(:emb AS vector) WHERE id = :pid"
                        ),
                        {"emb": vec_str, "pid": page_id},
                    )
                    await session.commit()
            else:
                raise

    async def embed_batch(self, items: list[tuple[str, str, dict]]) -> None:
        if not items:
            return

        from sqlalchemy.sql import text as sa_text

        stmt = sa_text("UPDATE wiki_pages SET embedding = CAST(:emb AS vector) WHERE id = :pid")
        # Chunked: one embedder request per slice (a whole generation level
        # in one request blew OpenAI's 300k-token cap), then one executemany
        # round-trip per slice.
        for chunk, texts in iter_embed_chunks(items):
            vectors = await self._embedder.embed(texts)
            expected_dim = len(vectors[0])
            await self._ensure_dimension(expected_dim)
            params = [
                {"emb": _encode(vector), "pid": page_id}
                for (page_id, _text, _meta), vector in zip(chunk, vectors, strict=True)
            ]
            try:
                async with self._session_factory() as session:
                    await session.execute(stmt, params)
                    await session.commit()
            except Exception as exc:
                if _is_dimension_mismatch_error(exc):
                    await self._migrate_vector_dim(expected_dim)
                    async with self._session_factory() as session:
                        await session.execute(stmt, params)
                        await session.commit()
                else:
                    raise

    async def upsert_vectors(self, items: list[tuple[str, list[float], dict]]) -> bool:
        """Raw-vector counterpart of :meth:`embed_and_upsert`, same semantics:
        UPDATE-only against ``wiki_pages``, so ids without a row (e.g. the
        synthetic ``decision:`` namespace) are silently skipped — this backend
        stores embeddings on page rows, not as standalone vectors. Returning
        True means the statement ran, not that every id matched a row.
        """
        if not items:
            return True

        expected_dim = len(items[0][1])
        await self._ensure_dimension(expected_dim)

        from sqlalchemy.sql import text as sa_text

        stmt = sa_text("UPDATE wiki_pages SET embedding = CAST(:emb AS vector) WHERE id = :pid")
        params = [
            {"emb": _encode([float(v) for v in vector]), "pid": page_id}
            for page_id, vector, _meta in items
        ]
        try:
            async with self._session_factory() as session:
                await session.execute(stmt, params)
                await session.commit()
        except Exception as exc:
            if _is_dimension_mismatch_error(exc):
                await self._migrate_vector_dim(expected_dim)
                async with self._session_factory() as session:
                    await session.execute(stmt, params)
                    await session.commit()
            else:
                raise
        return True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        q_vecs = await self._embedder.embed([query])
        return await self.search_by_vector(q_vecs[0], limit)

    async def search_by_vector(self, vector: list[float], limit: int = 10) -> list[SearchResult]:
        vec_str = _encode([float(v) for v in vector])

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
