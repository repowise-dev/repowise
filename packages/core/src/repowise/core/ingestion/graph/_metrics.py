"""Graph metric computation (PageRank, betweenness, communities, degrees).

These methods are mixed into :class:`GraphBuilder`. They read the metric
caches and ``_graph`` set up in the builder's ``__init__``.

Large-repo SQL routing
----------------------
When metric values have been materialized to SQL (the ``graph_metrics``
table), :meth:`load_metrics_from_sql` pre-fills the file-level caches so the
expensive NetworkX kernels (notably betweenness on 30k+ nodes) are never
recomputed — subsequent reads are served straight from the materialized
snapshot. The structural graph stays available for traversal.
"""

from __future__ import annotations

from typing import Any

import networkx as nx
import structlog

log = structlog.get_logger(__name__)

_LARGE_REPO_THRESHOLD = 30_000  # nodes — above this, algorithms are expensive


class MetricsMixin:
    """Centrality + community + degree metrics for :class:`GraphBuilder`."""

    # ------------------------------------------------------------------
    # SQL-backed metric routing (large repos)
    # ------------------------------------------------------------------

    def load_metrics_from_sql(self, metrics: dict[str, dict[str, Any]]) -> None:
        """Pre-fill the file-level metric caches from a materialized snapshot.

        *metrics* maps ``node_id`` → a dict with any of ``pagerank``,
        ``betweenness``, ``community_id``, ``in_degree``, ``out_degree``.
        After this call, the corresponding metric methods return the snapshot
        values without recomputing them via NetworkX.
        """
        self._pagerank_cache = {n: float(m.get("pagerank", 0.0)) for n, m in metrics.items()}
        self._betweenness_cache = {
            n: float(m.get("betweenness", 0.0)) for n, m in metrics.items()
        }
        self._community_cache = {n: int(m.get("community_id", 0)) for n, m in metrics.items()}
        self._in_degree_cache = {n: int(m.get("in_degree", 0)) for n, m in metrics.items()}
        self._out_degree_cache = {n: int(m.get("out_degree", 0)) for n, m in metrics.items()}
        log.info("graph.metrics_loaded_from_sql", nodes=len(metrics))

    def file_metrics_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return the file-level metrics as a ``node_id → metrics`` dict.

        Used to materialize the ``graph_metrics`` table. Computes every metric
        from NetworkX (or returns the cached/SQL-loaded values when present).
        """
        pr = self.pagerank()
        bc = self.betweenness_centrality()
        cd = self.community_detection()
        ind = self.in_degree()
        outd = self.out_degree()
        nodes = set(pr) | set(bc) | set(cd) | set(ind) | set(outd)
        return {
            n: {
                "pagerank": pr.get(n, 0.0),
                "betweenness": bc.get(n, 0.0),
                "community_id": cd.get(n, 0),
                "in_degree": ind.get(n, 0),
                "out_degree": outd.get(n, 0),
            }
            for n in nodes
        }

    # ------------------------------------------------------------------
    # Subgraphs
    # ------------------------------------------------------------------

    def file_subgraph(self) -> nx.DiGraph:
        """Return a subgraph containing only file-level nodes and import edges.

        Cached per build — five metric kernels (SCC, PageRank, betweenness,
        in/out degree) read it, and rebuilding the filtered copy per call is
        O(V+E) each time. The cache is guarded by ``_subgraph_lock`` because
        the init pipeline computes metrics concurrently via
        ``asyncio.to_thread``. Callers must treat the result as read-only.
        """
        cached = self._file_subgraph_cache
        if cached is not None:
            return cached
        with self._subgraph_lock:
            if self._file_subgraph_cache is not None:
                return self._file_subgraph_cache
            g = self.graph()
            file_nodes = [
                n
                for n, d in g.nodes(data=True)
                if d.get("node_type", "file") in ("file", "external")
            ]
            sub = g.subgraph(file_nodes).copy()
            edges_to_remove = [
                (u, v)
                for u, v, d in sub.edges(data=True)
                if d.get("edge_type") in ("co_changes",)
            ]
            sub.remove_edges_from(edges_to_remove)
            self._file_subgraph_cache = sub
            return sub

    def symbol_subgraph(self) -> nx.DiGraph:
        """Return a subgraph of symbol nodes connected by call + heritage edges.

        File-to-symbol ``defines`` edges and class-to-method ``has_method``
        ownership edges are dropped so that the resulting centrality
        scores reflect call/heritage flow rather than containment.

        Cached per build (see :meth:`file_subgraph` for the locking
        rationale). Callers must treat the result as read-only.
        """
        cached = self._symbol_subgraph_cache
        if cached is not None:
            return cached
        with self._subgraph_lock:
            if self._symbol_subgraph_cache is not None:
                return self._symbol_subgraph_cache
            g = self.graph()
            symbol_nodes = [n for n, d in g.nodes(data=True) if d.get("node_type") == "symbol"]
            sub = g.subgraph(symbol_nodes).copy()
            edges_to_remove = [
                (u, v)
                for u, v, d in sub.edges(data=True)
                if d.get("edge_type") not in ("calls", "extends", "implements")
            ]
            sub.remove_edges_from(edges_to_remove)
            self._symbol_subgraph_cache = sub
            return sub

    # ------------------------------------------------------------------
    # File-level metrics
    # ------------------------------------------------------------------

    def strongly_connected_components(self) -> list[frozenset[str]]:
        """Return SCCs as a list of frozensets."""
        return [frozenset(scc) for scc in nx.strongly_connected_components(self.file_subgraph())]

    def pagerank(self, alpha: float = 0.85) -> dict[str, float]:
        """Return PageRank scores for file nodes only (cached)."""
        if self._pagerank_cache is not None:
            return self._pagerank_cache
        filtered = self.file_subgraph()
        if filtered.number_of_nodes() == 0:
            self._pagerank_cache = {}
            return self._pagerank_cache

        try:
            self._pagerank_cache = nx.pagerank(filtered, alpha=alpha)
        except nx.PowerIterationFailedConvergence:
            log.warning("PageRank did not converge, using uniform scores")
            n = filtered.number_of_nodes()
            self._pagerank_cache = {node: 1.0 / n for node in filtered.nodes()}
        return self._pagerank_cache

    def betweenness_centrality(self) -> dict[str, float]:
        """Return betweenness centrality for file nodes (cached)."""
        if self._betweenness_cache is not None:
            return self._betweenness_cache
        g = self.file_subgraph()
        n = g.number_of_nodes()
        if n == 0:
            self._betweenness_cache = {}
            return self._betweenness_cache
        if n > _LARGE_REPO_THRESHOLD:
            k = min(500, n)
            self._betweenness_cache = nx.betweenness_centrality(g, k=k, normalized=True)
        else:
            from ._betweenness import betweenness_centrality_fast

            self._betweenness_cache = betweenness_centrality_fast(g, normalized=True)
        return self._betweenness_cache

    def in_degree(self) -> dict[str, int]:
        """Return in-degree (number of importers) for each file node (cached)."""
        if self._in_degree_cache is not None:
            return self._in_degree_cache
        g = self.file_subgraph()
        self._in_degree_cache = {n: int(d) for n, d in g.in_degree()}
        return self._in_degree_cache

    def out_degree(self) -> dict[str, int]:
        """Return out-degree (number of dependencies) for each file node (cached)."""
        if self._out_degree_cache is not None:
            return self._out_degree_cache
        g = self.file_subgraph()
        self._out_degree_cache = {n: int(d) for n, d in g.out_degree()}
        return self._out_degree_cache

    # ------------------------------------------------------------------
    # Communities
    # ------------------------------------------------------------------

    def community_detection(self) -> dict[str, int]:
        """Assign a community ID to each file node."""
        if self._community_cache is not None:
            return self._community_cache

        from repowise.core.analysis.communities import detect_file_communities

        try:
            assignment, info, algo = detect_file_communities(self._graph)
            self._community_cache = assignment
            self._community_info_cache = info
            self._community_algo = algo
        except Exception as exc:
            log.warning("community_detection_failed", error=str(exc))
            file_nodes = [
                n for n, d in self._graph.nodes(data=True) if d.get("node_type", "file") == "file"
            ]
            self._community_cache = {n: 0 for n in file_nodes}
            self._community_info_cache = {}
            self._community_algo = "failed"
        return self._community_cache

    def symbol_communities(self) -> dict[str, int]:
        """Assign a community ID to each symbol node using call/heritage edges."""
        if self._symbol_community_cache is not None:
            return self._symbol_community_cache

        from repowise.core.analysis.communities import detect_symbol_communities

        try:
            self._symbol_community_cache = detect_symbol_communities(self._graph)
        except Exception as exc:
            log.warning("symbol_community_detection_failed", error=str(exc))
            self._symbol_community_cache = {}
        return self._symbol_community_cache

    def community_info(self) -> dict[int, Any]:
        """Return metadata for each file-level community."""
        if self._community_info_cache is None:
            self.community_detection()
        return self._community_info_cache or {}

    # ------------------------------------------------------------------
    # Symbol-level metrics
    # ------------------------------------------------------------------

    def symbol_pagerank(self, alpha: float = 0.85) -> dict[str, float]:
        """Return PageRank scores for symbol nodes only (cached).

        Computed on the call/heritage symbol subgraph — this is what the
        UI's per-symbol "graph metrics" panel reads. Without it every
        symbol shows ``Not indexed in graph``.
        """
        if self._symbol_pagerank_cache is not None:
            return self._symbol_pagerank_cache
        sub = self.symbol_subgraph()
        if sub.number_of_nodes() == 0:
            self._symbol_pagerank_cache = {}
            return self._symbol_pagerank_cache
        try:
            self._symbol_pagerank_cache = nx.pagerank(sub, alpha=alpha)
        except nx.PowerIterationFailedConvergence:
            log.warning("Symbol PageRank did not converge, using uniform scores")
            n = sub.number_of_nodes()
            self._symbol_pagerank_cache = {node: 1.0 / n for node in sub.nodes()}
        return self._symbol_pagerank_cache

    def symbol_betweenness_centrality(self) -> dict[str, float]:
        """Return betweenness centrality for symbol nodes (cached)."""
        if self._symbol_betweenness_cache is not None:
            return self._symbol_betweenness_cache
        sub = self.symbol_subgraph()
        n = sub.number_of_nodes()
        if n == 0:
            self._symbol_betweenness_cache = {}
            return self._symbol_betweenness_cache
        if n > _LARGE_REPO_THRESHOLD:
            k = min(500, n)
            self._symbol_betweenness_cache = nx.betweenness_centrality(sub, k=k, normalized=True)
        else:
            from ._betweenness import betweenness_centrality_fast

            self._symbol_betweenness_cache = betweenness_centrality_fast(sub, normalized=True)
        return self._symbol_betweenness_cache

    # ------------------------------------------------------------------
    # Execution flows + bulk priming
    # ------------------------------------------------------------------

    def execution_flows(self, config: Any | None = None) -> Any:
        """Trace execution flows from entry-point symbols (cached when ``config`` is None)."""
        from repowise.core.analysis.execution_flows import (
            ExecutionFlowReport,
            trace_execution_flows,
        )

        # Only the no-config path is cached — callers that pass custom
        # FlowConfig still get a fresh trace.
        if config is None and self._execution_flow_cache is not None:
            return self._execution_flow_cache

        file_cd = self.community_detection()
        merged_cd: dict[str, int] = dict(file_cd)

        sym_cd = self.symbol_communities()
        merged_cd.update(sym_cd)

        for node_id in self._graph.nodes():
            if node_id not in merged_cd and "::" in node_id:
                file_path = node_id.split("::")[0]
                if file_path in file_cd:
                    merged_cd[node_id] = file_cd[file_path]

        try:
            report = trace_execution_flows(self._graph, merged_cd, config)
        except Exception as exc:
            log.warning("execution_flow_tracing_failed", error=str(exc))
            report = ExecutionFlowReport(
                total_entry_points_scored=0,
                total_flows=0,
                flows=[],
            )
        if config is None:
            self._execution_flow_cache = report
        return report

    async def compute_metrics_parallel(self) -> None:
        """Eagerly populate all metric caches with fan-out parallelism.

        Runs PageRank, betweenness, file/symbol community detection in
        parallel via ``asyncio.gather`` + ``asyncio.to_thread`` (the
        scipy- and igraph-backed kernels release the GIL during heavy
        compute, so true parallelism is achievable). Execution flows then
        run after, since they depend on the community caches.

        Calling this is optional — every metric falls back to lazy
        computation, so existing call sites keep working unchanged.
        """
        import asyncio as _asyncio

        await _asyncio.gather(
            _asyncio.to_thread(self.pagerank),
            _asyncio.to_thread(self.betweenness_centrality),
            _asyncio.to_thread(self.symbol_pagerank),
            _asyncio.to_thread(self.symbol_betweenness_centrality),
            _asyncio.to_thread(self.community_detection),
            _asyncio.to_thread(self.symbol_communities),
        )
        # execution_flows reads from the community caches just primed above.
        await _asyncio.to_thread(self.execution_flows)

    def _build_scc_map(self) -> dict[str, int]:
        """Assign a numeric SCC ID to each node."""
        result: dict[str, int] = {}
        for scc_id, scc in enumerate(nx.strongly_connected_components(self.graph())):
            for node in scc:
                result[node] = scc_id
        return result
