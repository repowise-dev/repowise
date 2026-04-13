"""Dependency graph builder for the repowise ingestion pipeline.

GraphBuilder constructs a directed graph from ParsedFile objects with two
tiers of nodes:

    File-level nodes:
        "file"     — every source file
        "external" — third-party / unresolvable imports (prefix "external:")

    Symbol-level nodes:
        "symbol"   — functions, classes, methods, interfaces, etc.
                     keyed by Symbol.id (e.g. "src/app.py::main")

Edge types:
    "imports"     — file-to-file import relationship
    "defines"     — file-to-symbol containment
    "has_method"  — class-to-method ownership
    "calls"       — symbol-to-symbol call relationship (with confidence)

After calling build(), graph metrics are available:
    pagerank()                  — dict[path, float]
    strongly_connected_components() — list[frozenset[str]]
    betweenness_centrality()    — dict[path, float]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx
import structlog

from .models import ParsedFile
from .resolvers import ResolverContext, resolve_import
from .resolvers.go import read_go_module_path

log = structlog.get_logger(__name__)

_LARGE_REPO_THRESHOLD = 30_000  # nodes — above this, algorithms are expensive

# Path segments that mark a file as low-value for stem-based import resolution.
_LOW_VALUE_PATH_SEGMENTS = frozenset(
    {
        "tests",
        "test",
        "_tests",
        "__tests__",
        "testing",
        "test_apps",
        "testdata",
        "test_data",
        "fixtures",
        "examples",
        "example",
        "samples",
        "sample",
        "scripts",
        "benchmarks",
        "bench",
        "docs",
        "doc",
    }
)


def _stem_priority(path: str, stem: str) -> tuple[int, int, int, str]:
    """Sort key for choosing among files that share an import stem.

    Lower tuples sort first; callers take ``candidates[0]`` as the resolution.
    """
    path_obj = Path(path)
    parts = path_obj.parts
    if path_obj.name == "__init__.py":
        parent_match = 0
    else:
        parent_dir = parts[-2].lower() if len(parts) >= 2 else ""
        parent_match = 0 if parent_dir == stem else 1
    low_value = 1 if any(seg.lower() in _LOW_VALUE_PATH_SEGMENTS for seg in parts) else 0
    return (parent_match, low_value, len(parts), path)


class GraphBuilder:
    """Build a dependency graph from a collection of ParsedFile objects.

    Usage::

        builder = GraphBuilder()
        for parsed in parsed_files:
            builder.add_file(parsed)
        graph = builder.build()
        pr = builder.pagerank()
    """

    def __init__(self, repo_path: Path | str | None = None) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._parsed_files: dict[str, ParsedFile] = {}  # path → ParsedFile
        self._built = False
        self._repo_path: Path | None = Path(repo_path) if repo_path else None
        self._tsconfig_resolver: Any | None = None  # TsconfigResolver (lazy import)

        # Community / flow caches (invalidated on build)
        self._community_cache: dict[str, int] | None = None
        self._symbol_community_cache: dict[str, int] | None = None
        self._community_info_cache: dict[int, Any] | None = None
        self._community_algo: str = ""

    def set_tsconfig_resolver(self, resolver: Any) -> None:
        """Attach a :class:`TsconfigResolver` for TS/JS path-alias resolution."""
        self._tsconfig_resolver = resolver

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def add_file(self, parsed: ParsedFile) -> None:
        """Register one parsed file and its symbols in the graph."""
        path = parsed.file_info.path
        self._parsed_files[path] = parsed
        self._built = False  # invalidate cached metrics

        # --- File node ---
        self._graph.add_node(
            path,
            node_type="file",
            language=parsed.file_info.language,
            symbol_count=len(parsed.symbols),
            has_error=bool(parsed.parse_errors),
            is_test=parsed.file_info.is_test,
            is_entry_point=parsed.file_info.is_entry_point,
        )

        # --- Symbol nodes ---
        for sym in parsed.symbols:
            self._graph.add_node(
                sym.id,
                node_type="symbol",
                kind=sym.kind,
                name=sym.name,
                qualified_name=sym.qualified_name,
                file_path=path,
                start_line=sym.start_line,
                end_line=sym.end_line,
                visibility=sym.visibility,
                is_async=sym.is_async,
                language=sym.language,
                parent_name=sym.parent_name,
                signature=sym.signature,
            )

            # DEFINES edge: file → symbol
            self._graph.add_edge(
                path,
                sym.id,
                edge_type="defines",
            )

            # HAS_METHOD edge: class/struct → method
            if sym.parent_name and sym.kind == "method":
                parent_id = f"{path}::{sym.parent_name}"
                if parent_id in self._graph:
                    self._graph.add_edge(
                        parent_id,
                        sym.id,
                        edge_type="has_method",
                    )

    def build(self) -> nx.DiGraph:
        """Resolve imports and calls, add edges. Returns the finalized graph."""
        # Invalidate cached metrics
        self._community_cache = None
        self._symbol_community_cache = None
        self._community_info_cache = None
        self._community_algo = ""

        # Clear import/call edges but keep structural edges (defines, has_method)
        edges_to_remove = [
            (u, v)
            for u, v, d in self._graph.edges(data=True)
            if d.get("edge_type") not in ("defines", "has_method")
        ]
        self._graph.remove_edges_from(edges_to_remove)

        # Build lookup tables for import resolution
        path_set = set(self._parsed_files.keys())
        stem_map = self._build_stem_map(path_set)

        # Construct resolver context
        ctx = ResolverContext(
            path_set=path_set,
            stem_map=stem_map,
            graph=self._graph,
            repo_path=self._repo_path,
            tsconfig_resolver=self._tsconfig_resolver,
            go_module_path=read_go_module_path(self._repo_path),
            parsed_files=self._parsed_files,
        )

        # --- Phase 1: Resolve file-level imports ---
        import_targets: dict[str, set[str]] = {}  # file → set of imported files

        for path, parsed in self._parsed_files.items():
            file_imports: set[str] = set()
            for imp in parsed.imports:
                target = resolve_import(imp.module_path, path, parsed.file_info.language, ctx)
                if target:
                    imp.resolved_file = target
                    file_imports.add(target)
                    # Aggregate imported_names on parallel edges
                    if self._graph.has_edge(path, target):
                        existing = self._graph[path][target].get("imported_names", [])
                        merged = list(set(existing + imp.imported_names))
                        self._graph[path][target]["imported_names"] = merged
                    else:
                        self._graph.add_edge(
                            path,
                            target,
                            edge_type="imports",
                            imported_names=list(imp.imported_names),
                        )
            import_targets[path] = file_imports

        # --- Phase 2: Resolve heritage (extends/implements) ---
        self._resolve_heritage(import_targets)

        # --- Phase 3: Resolve symbol-level calls ---
        self._resolve_calls(import_targets)

        self._built = True

        # Count edge types for logging
        edge_counts: dict[str, int] = {}
        for _, _, d in self._graph.edges(data=True):
            et = d.get("edge_type", "imports")
            edge_counts[et] = edge_counts.get(et, 0) + 1

        file_nodes = sum(
            1 for _, d in self._graph.nodes(data=True) if d.get("node_type", "file") == "file"
        )
        symbol_nodes = sum(
            1 for _, d in self._graph.nodes(data=True) if d.get("node_type") == "symbol"
        )

        log.info(
            "Graph built",
            file_nodes=file_nodes,
            symbol_nodes=symbol_nodes,
            edges=self._graph.number_of_edges(),
            edge_types=edge_counts,
        )
        return self._graph

    def _resolve_heritage(self, import_targets: dict[str, set[str]]) -> None:
        """Resolve heritage relations and add EXTENDS/IMPLEMENTS edges."""
        from .heritage_resolver import HeritageResolver

        resolver = HeritageResolver(self._parsed_files, import_targets)
        total_resolved = 0

        for path, parsed in self._parsed_files.items():
            if not parsed.heritage:
                continue

            resolved = resolver.resolve_file(path, parsed.heritage)
            for rh in resolved:
                if rh.child_id in self._graph and rh.parent_id in self._graph:
                    if not self._graph.has_edge(rh.child_id, rh.parent_id):
                        self._graph.add_edge(
                            rh.child_id,
                            rh.parent_id,
                            edge_type=rh.edge_type,
                            confidence=rh.confidence,
                        )
                        total_resolved += 1
                    else:
                        existing = self._graph[rh.child_id][rh.parent_id]
                        if rh.confidence > existing.get("confidence", 0):
                            existing["confidence"] = rh.confidence

        log.info("Heritage edges resolved", total=total_resolved)

    def _resolve_calls(self, import_targets: dict[str, set[str]]) -> None:
        """Run three-tier call resolution and add CALLS edges to the graph."""
        from .call_resolver import CallResolver

        resolver = CallResolver(self._parsed_files, import_targets)
        total_resolved = 0

        for path, parsed in self._parsed_files.items():
            if not parsed.calls:
                continue

            resolved = resolver.resolve_file(path, parsed.calls)
            for rc in resolved:
                if rc.caller_id in self._graph and rc.callee_id in self._graph:
                    if not self._graph.has_edge(rc.caller_id, rc.callee_id):
                        self._graph.add_edge(
                            rc.caller_id,
                            rc.callee_id,
                            edge_type="calls",
                            confidence=rc.confidence,
                        )
                        total_resolved += 1
                    else:
                        existing = self._graph[rc.caller_id][rc.callee_id]
                        if rc.confidence > existing.get("confidence", 0):
                            existing["confidence"] = rc.confidence

        log.info("Call edges resolved", total=total_resolved)

    def graph(self) -> nx.DiGraph:
        """Return the graph (building it first if necessary)."""
        if not self._built:
            self.build()
        return self._graph

    # ------------------------------------------------------------------
    # Graph metrics
    # ------------------------------------------------------------------

    def strongly_connected_components(self) -> list[frozenset[str]]:
        """Return SCCs as a list of frozensets."""
        return [frozenset(scc) for scc in nx.strongly_connected_components(self.file_subgraph())]

    def betweenness_centrality(self) -> dict[str, float]:
        """Return betweenness centrality for file nodes."""
        g = self.file_subgraph()
        n = g.number_of_nodes()
        if n == 0:
            return {}
        if n > _LARGE_REPO_THRESHOLD:
            k = min(500, n)
            return nx.betweenness_centrality(g, k=k, normalized=True)
        return nx.betweenness_centrality(g, normalized=True)

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

    def execution_flows(self, config: Any | None = None) -> Any:
        """Trace execution flows from entry-point symbols."""
        from repowise.core.analysis.execution_flows import (
            ExecutionFlowReport,
            trace_execution_flows,
        )

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
            return trace_execution_flows(self._graph, merged_cd, config)
        except Exception as exc:
            log.warning("execution_flow_tracing_failed", error=str(exc))
            return ExecutionFlowReport(
                total_entry_points_scored=0,
                total_flows=0,
                flows=[],
            )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialize the graph to a JSON-compatible dict (node-link format)."""
        return nx.node_link_data(self.graph())

    async def persist(self, db_path: Path, repo_id: str) -> None:
        """Persist the graph to an SQLite database."""
        import aiosqlite

        pr = self.pagerank()
        bc = self.betweenness_centrality()
        scc_map = self._build_scc_map()
        g = self.graph()

        async with aiosqlite.connect(db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    repo_id      TEXT NOT NULL,
                    path         TEXT NOT NULL,
                    language     TEXT,
                    symbol_count INTEGER,
                    has_error    INTEGER,
                    pagerank     REAL,
                    betweenness  REAL,
                    scc_id       INTEGER,
                    PRIMARY KEY (repo_id, path)
                );
                CREATE TABLE IF NOT EXISTS graph_edges (
                    repo_id        TEXT NOT NULL,
                    source_path    TEXT NOT NULL,
                    target_path    TEXT NOT NULL,
                    imported_names TEXT,
                    PRIMARY KEY (repo_id, source_path, target_path)
                );
            """)

            node_rows = [
                (
                    repo_id,
                    path,
                    data.get("language", ""),
                    data.get("symbol_count", 0),
                    int(data.get("has_error", False)),
                    pr.get(path, 0.0),
                    bc.get(path, 0.0),
                    scc_map.get(path, 0),
                )
                for path, data in g.nodes(data=True)
            ]
            await db.executemany(
                "INSERT OR REPLACE INTO graph_nodes VALUES (?,?,?,?,?,?,?,?)",
                node_rows,
            )

            edge_rows = [
                (
                    repo_id,
                    src,
                    dst,
                    json.dumps(data.get("imported_names", [])),
                )
                for src, dst, data in g.edges(data=True)
            ]
            await db.executemany(
                "INSERT OR REPLACE INTO graph_edges VALUES (?,?,?,?)",
                edge_rows,
            )

            await db.commit()

        log.info(
            "Graph persisted",
            db_path=str(db_path),
            nodes=len(node_rows),
            edges=len(edge_rows),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_stem_map(self, path_set: set[str]) -> dict[str, list[str]]:
        """Map import-stems to candidate file paths, sorted best-first."""
        buckets: dict[str, list[str]] = {}
        for p in path_set:
            path_obj = Path(p)
            if path_obj.name == "__init__.py":
                parent = path_obj.parent.name
                if not parent:
                    continue
                stem = parent.lower()
            else:
                stem = path_obj.stem.lower()
            buckets.setdefault(stem, []).append(p)

        for stem, paths in buckets.items():
            paths.sort(key=lambda candidate: _stem_priority(candidate, stem))
        return buckets

    # ------------------------------------------------------------------
    # Co-change edges
    # ------------------------------------------------------------------

    def add_co_change_edges(self, git_meta_map: dict, min_count: int = 3) -> int:
        """Add co_changes edges from git metadata. Returns count of edges added."""
        count = 0
        seen: set[tuple[str, str]] = set()

        for file_path, meta in git_meta_map.items():
            co_json = meta.get("co_change_partners_json", "[]")
            if isinstance(co_json, str):
                try:
                    partners = json.loads(co_json)
                except Exception:
                    partners = []
            else:
                partners = co_json

            for partner in partners:
                partner_path = partner.get("file_path", "")
                co_count = partner.get("co_change_count", 0)
                if co_count < min_count:
                    continue
                if partner_path not in self._graph:
                    continue

                pair = tuple(sorted([file_path, partner_path]))
                if pair in seen:
                    continue
                seen.add(pair)

                if not self._graph.has_edge(file_path, partner_path) and not self._graph.has_edge(
                    partner_path, file_path
                ):
                    self._graph.add_edge(
                        file_path,
                        partner_path,
                        edge_type="co_changes",
                        weight=co_count,
                        imported_names=[],
                    )
                    count += 1

        log.info("Co-change edges added", count=count)
        return count

    def update_co_change_edges(self, updated_meta: dict, min_count: int = 3) -> None:
        """Remove old co_changes edges for updated files, add new ones."""
        edges_to_remove = []
        for u, v, data in self._graph.edges(data=True):
            if data.get("edge_type") == "co_changes" and (u in updated_meta or v in updated_meta):
                edges_to_remove.append((u, v))
        self._graph.remove_edges_from(edges_to_remove)
        self.add_co_change_edges(updated_meta, min_count)

    # ------------------------------------------------------------------
    # Dynamic-hint edges
    # ------------------------------------------------------------------

    def add_dynamic_edges(self, edges: list) -> None:
        """Add dynamic-hint edges to the graph. Each edge is a DynamicEdge."""
        for e in edges:
            if e.source not in self._graph:
                continue
            if e.target not in self._graph:
                self._graph.add_node(e.target)
            self._graph.add_edge(
                e.source,
                e.target,
                edge_type="dynamic",
                hint_source=e.hint_source,
                weight=e.weight,
            )

    # ------------------------------------------------------------------
    # Framework-aware synthetic edges
    # ------------------------------------------------------------------

    def add_framework_edges(self, tech_stack: list[str] | None = None) -> int:
        """Add synthetic edges for framework-mediated relationships.

        Returns the number of edges added.
        """
        from .framework_edges import add_framework_edges

        path_set = set(self._parsed_files.keys())
        stem_map = self._build_stem_map(path_set)

        ctx = ResolverContext(
            path_set=path_set,
            stem_map=stem_map,
            graph=self._graph,
            repo_path=self._repo_path,
            tsconfig_resolver=self._tsconfig_resolver,
            go_module_path=read_go_module_path(self._repo_path),
            parsed_files=self._parsed_files,
        )

        count = add_framework_edges(self._graph, self._parsed_files, ctx, tech_stack)
        if count:
            log.info("Framework edges added", count=count)
        return count

    def file_subgraph(self) -> nx.DiGraph:
        """Return a subgraph containing only file-level nodes and import edges."""
        g = self.graph()
        file_nodes = [
            n for n, d in g.nodes(data=True) if d.get("node_type", "file") in ("file", "external")
        ]
        sub = g.subgraph(file_nodes).copy()
        edges_to_remove = [
            (u, v) for u, v, d in sub.edges(data=True) if d.get("edge_type") in ("co_changes",)
        ]
        sub.remove_edges_from(edges_to_remove)
        return sub

    def pagerank(self, alpha: float = 0.85) -> dict[str, float]:
        """Return PageRank scores for file nodes only."""
        filtered = self.file_subgraph()
        if filtered.number_of_nodes() == 0:
            return {}

        try:
            return nx.pagerank(filtered, alpha=alpha)
        except nx.PowerIterationFailedConvergence:
            log.warning("PageRank did not converge, using uniform scores")
            n = filtered.number_of_nodes()
            return {node: 1.0 / n for node in filtered.nodes()}

    def _build_scc_map(self) -> dict[str, int]:
        """Assign a numeric SCC ID to each node."""
        result: dict[str, int] = {}
        for scc_id, scc in enumerate(nx.strongly_connected_components(self.graph())):
            for node in scc:
                result[node] = scc_id
        return result
