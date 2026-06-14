"""Atomic three-store transaction coordinator.

Buffers writes across SQL (AsyncSession), in-memory graph (GraphBuilder /
NetworkX), and the vector store. Flushes them in order; rolls back on failure.

Usage:
    coord = AtomicStorageCoordinator(session, graph_builder, vector_store)
    async with coord.transaction() as txn:
        txn.add_sql(some_orm_obj)
        txn.add_graph_node("path/file.py", attrs={...})
        txn.add_graph_edge("a.py", "b.py", attrs={...})
        txn.add_vector("page-id", {"path": ..., "summary": ..., "embedding": ...})
    # On normal exit: SQL commit, graph applied, vectors upserted.
    # On exception anywhere: SQL rollback, graph nodes/edges removed, vector ids deleted.

Vector store notes
------------------
All three stores (InMemoryVectorStore, LanceDBVectorStore, PgVectorStore) share
the same async API:
  - upsert:  embed_and_upsert(page_id: str, text: str, metadata: dict) -> None
  - delete:  delete(page_id: str) -> None
  - count:   __len__() (InMemoryVectorStore only; others unsupported)

The ``record`` dict passed to ``add_vector`` must contain a ``"text"`` key
(the raw text to embed).  All other keys are forwarded as metadata.

GraphBuilder notes
------------------
The NetworkX DiGraph is stored as ``GraphBuilder._graph`` (private attribute).
This coordinator accesses it directly via ``getattr(builder, "_graph", None)``
to avoid triggering a full ``build()`` call.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
import structlog

log = structlog.get_logger(__name__)


@dataclass
class _PendingTransaction:
    pending_sql_objects: list[Any] = field(default_factory=list)
    pending_graph_nodes: list[tuple[str, dict]] = field(default_factory=list)
    pending_graph_edges: list[tuple[str, str, dict]] = field(default_factory=list)
    pending_vectors: list[tuple[str, dict]] = field(default_factory=list)  # (id, record)

    def add_sql(self, obj: Any) -> None:
        self.pending_sql_objects.append(obj)

    def add_graph_node(self, node_id: str, attrs: dict | None = None) -> None:
        self.pending_graph_nodes.append((node_id, attrs or {}))

    def add_graph_edge(self, src: str, dst: str, attrs: dict | None = None) -> None:
        self.pending_graph_edges.append((src, dst, attrs or {}))

    def add_vector(self, vector_id: str, record: dict) -> None:
        """Queue a vector upsert.

        ``record`` must contain a ``"text"`` key with the raw text to embed.
        All remaining keys are passed as metadata to the vector store.
        """
        self.pending_vectors.append((vector_id, record))


class AtomicStorageCoordinator:
    def __init__(self, session, graph_builder=None, vector_store=None) -> None:
        self._session = session
        self._graph_builder = graph_builder
        self._vector_store = vector_store
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_PendingTransaction]:
        txn = _PendingTransaction()
        applied_nodes: list[str] = []
        applied_edges: list[tuple[str, str]] = []
        applied_vector_ids: list[str] = []
        async with self._lock:
            try:
                yield txn
                # === FLUSH ===
                # 1. SQL first (most likely to fail constraints)
                for obj in txn.pending_sql_objects:
                    self._session.add(obj)
                await self._session.flush()

                # 2. Graph (in-memory; track for rollback)
                # Access _graph directly to avoid triggering a full build() call.
                if self._graph_builder is not None:
                    g = getattr(self._graph_builder, "_graph", None)
                    if g is not None:
                        for node_id, attrs in txn.pending_graph_nodes:
                            existed = node_id in g
                            g.add_node(node_id, **attrs)
                            if not existed:
                                applied_nodes.append(node_id)
                        for src, dst, attrs in txn.pending_graph_edges:
                            if not g.has_edge(src, dst):
                                g.add_edge(src, dst, **attrs)
                                applied_edges.append((src, dst))

                # 3. Vector store last
                # All stores expose:  embed_and_upsert(page_id, text, metadata) async
                if self._vector_store is not None and txn.pending_vectors:
                    for vid, record in txn.pending_vectors:
                        await _vector_upsert(self._vector_store, vid, record)
                        applied_vector_ids.append(vid)

                await self._session.commit()
                log.debug(
                    "atomic_txn_committed",
                    sql=len(txn.pending_sql_objects),
                    nodes=len(applied_nodes),
                    edges=len(applied_edges),
                    vectors=len(applied_vector_ids),
                )
            except Exception as e:
                log.warning("atomic_txn_failed_rollback", error=str(e))
                # SQL rollback
                try:
                    await self._session.rollback()
                except Exception as e2:
                    log.error("sql_rollback_failed", error=str(e2))
                # Graph rollback
                if self._graph_builder is not None:
                    g = getattr(self._graph_builder, "_graph", None)
                    if g is not None:
                        for src, dst in applied_edges:
                            if g.has_edge(src, dst):
                                g.remove_edge(src, dst)
                        for node_id in applied_nodes:
                            if node_id in g:
                                g.remove_node(node_id)
                # Vector rollback — all stores expose delete(page_id) async
                if self._vector_store is not None:
                    for vid in applied_vector_ids:
                        try:
                            await _vector_delete(self._vector_store, vid)
                        except Exception as e2:
                            log.error("vector_rollback_failed", id=vid, error=str(e2))
                raise

    async def health_check(self) -> dict:
        """Compare counts across stores, *per population*. Returns a drift report.

        The page vector store is shared: it holds wiki-page vectors **and**
        decision-record vectors (under the ``decision:<id>`` namespace). A naive
        ``wiki_pages`` vs *total vector count* comparison therefore compares
        unlike populations — a store with only decision vectors reads as 100%
        page drift even though nothing is wrong with the pages (issue #374).

        This compares like with like:
          * ``wiki_pages``        <-> page vectors  -> ``page_drift``
          * ``decision_records``  <-> decision vectors -> ``decision_drift``

        ``drift`` is kept as an alias of ``page_drift`` for backwards
        compatibility. ``vector_count`` remains the total (page + decision) so
        existing callers that only display it are unaffected.
        """
        from sqlalchemy import text
        report: dict = {
            "sql_pages": None,
            "sql_decisions": None,
            "vector_count": None,
            "vector_page_count": None,
            "vector_decision_count": None,
            "graph_nodes": None,
            "drift": None,
            "page_drift": None,
            "decision_drift": None,
        }
        try:
            res = await self._session.execute(text("SELECT COUNT(*) FROM wiki_pages"))
            report["sql_pages"] = int(res.scalar() or 0)
        except Exception as e:
            report["sql_pages_error"] = str(e)
        try:
            res = await self._session.execute(text("SELECT COUNT(*) FROM decision_records"))
            report["sql_decisions"] = int(res.scalar() or 0)
        except Exception as e:
            report["sql_decisions_error"] = str(e)
        if self._graph_builder is not None:
            g = getattr(self._graph_builder, "_graph", None)
            if g is not None:
                report["graph_nodes"] = g.number_of_nodes()
        if report["graph_nodes"] is None:
            try:
                res = await self._session.execute(text("SELECT COUNT(*) FROM graph_nodes"))
                report["graph_nodes"] = int(res.scalar() or 0)
            except Exception as e:
                report["graph_nodes_error"] = str(e)
        if self._vector_store is not None:
            try:
                breakdown = await _vector_breakdown(self._vector_store)
                report["vector_count"] = breakdown["total"]
                report["vector_page_count"] = breakdown["page_count"]
                report["vector_decision_count"] = breakdown["decision_count"]
            except Exception as e:
                report["vector_count_error"] = str(e)

        # Page drift: wiki_pages vs page vectors (like with like).
        if report["sql_pages"] is not None and report["vector_page_count"] is not None:
            denom = max(report["sql_pages"], 1)
            report["page_drift"] = abs(report["sql_pages"] - report["vector_page_count"]) / denom
        # Decision drift: decision_records vs decision vectors.
        if report["sql_decisions"] is not None and report["vector_decision_count"] is not None:
            denom = max(report["sql_decisions"], 1)
            report["decision_drift"] = (
                abs(report["sql_decisions"] - report["vector_decision_count"]) / denom
            )
        # Backwards-compatible alias.
        report["drift"] = report["page_drift"]
        return report


# ---------------------------------------------------------------------------
# Vector store adapter helpers
#
# All three stores (InMemoryVectorStore, LanceDBVectorStore, PgVectorStore)
# share the same method names:
#   upsert: embed_and_upsert(page_id: str, text: str, metadata: dict) -> None  (async)
#   delete: delete(page_id: str) -> None  (async)
#   count:  __len__() (sync, InMemoryVectorStore only; others return -1)
# ---------------------------------------------------------------------------

async def _vector_upsert(store, vid: str, record: dict) -> None:
    """Call embed_and_upsert on the store.

    ``record`` must contain a ``"text"`` key.  All other keys are forwarded
    as metadata.  Raises ValueError if ``"text"`` is absent.
    """
    text = record.get("text")
    if text is None:
        raise ValueError(
            f"_vector_upsert: record for '{vid}' is missing required 'text' key. "
            f"Keys present: {list(record.keys())}"
        )
    metadata = {k: v for k, v in record.items() if k != "text"}
    await store.embed_and_upsert(vid, text, metadata)


async def _vector_delete(store, vid: str) -> None:
    """Call delete(page_id) on the store."""
    await store.delete(vid)


async def _vector_count(store) -> int:
    """Return the number of vectors in the store.

    InMemoryVectorStore exposes __len__; LanceDB and PgVector are counted by
    listing page IDs (cheap on small/medium repos and avoids backend-specific SQL).
    Returns -1 if no count method is available.
    """
    fn = getattr(store, "__len__", None)
    if fn is not None:
        return int(fn())
    list_ids = getattr(store, "list_page_ids", None)
    if list_ids is not None:
        ids = await list_ids()
        return len(ids)
    return -1


async def _vector_breakdown(store) -> dict:
    """Split the store's vectors into page vs decision populations.

    The page vector store co-locates wiki-page vectors and decision-record
    vectors (the latter under the ``decision:<id>`` namespace). Health checks
    must compare each population against its own SQL table, so this enumerates
    the stored ids and buckets them by namespace.

    Returns ``{"total", "page_count", "decision_count"}``. When the store can't
    enumerate ids (only a total count is available) the population counts are
    ``None`` and ``total`` falls back to :func:`_vector_count` (which may be -1
    for unsupported stores).
    """
    from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX

    list_ids = getattr(store, "list_page_ids", None)
    if list_ids is not None:
        ids = await list_ids()
        if ids:
            decision = sum(1 for i in ids if i.startswith(DECISION_VECTOR_PREFIX))
            return {
                "total": len(ids),
                "page_count": len(ids) - decision,
                "decision_count": decision,
            }
        # An empty id set is ambiguous: the store may genuinely be empty, or it
        # may not implement id enumeration. Cross-check against the count.
        total = await _vector_count(store)
        if total <= 0:
            return {"total": max(total, 0), "page_count": 0, "decision_count": 0}
        return {"total": total, "page_count": None, "decision_count": None}

    total = await _vector_count(store)
    return {"total": total, "page_count": None, "decision_count": None}
