"""Dependency graph builder for the repowise ingestion pipeline.

GraphBuilder constructs a directed multigraph from ParsedFile objects.

Node types:
    "file"     — every source file
    "external" — third-party / unresolvable imports (prefix "external:")

Edge attributes:
    imported_names: list[str] — specific names imported across this edge

After calling build(), graph metrics are available:
    pagerank()                  — dict[path, float]
    strongly_connected_components() — list[frozenset[str]]
    betweenness_centrality()    — dict[path, float]

Graph persistence uses a lightweight SQLite schema (two tables: graph_nodes,
graph_edges).  Phase 4 will replace this with the full SQLAlchemy schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx
import structlog

from .models import ParsedFile

log = structlog.get_logger(__name__)

_LARGE_REPO_THRESHOLD = 30_000  # nodes — above this, algorithms are expensive


class GraphBuilder:
    """Build a dependency graph from a collection of ParsedFile objects.

    Usage::

        builder = GraphBuilder()
        for parsed in parsed_files:
            builder.add_file(parsed)
        graph = builder.build()
        pr = builder.pagerank()
    """

    def __init__(self) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._parsed_files: dict[str, ParsedFile] = {}  # path → ParsedFile
        self._built = False

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def add_file(self, parsed: ParsedFile) -> None:
        """Register one parsed file in the graph."""
        path = parsed.file_info.path
        self._parsed_files[path] = parsed
        self._built = False  # invalidate cached metrics
        self._graph.add_node(
            path,
            language=parsed.file_info.language,
            symbol_count=len(parsed.symbols),
            has_error=bool(parsed.parse_errors),
            is_test=parsed.file_info.is_test,
            is_entry_point=parsed.file_info.is_entry_point,
        )

    def build(self) -> nx.DiGraph:
        """Resolve imports and add edges. Returns the finalized graph.

        Idempotent: can be called multiple times; re-resolves edges each time.
        """
        # Clear old edges, keep nodes
        self._graph.remove_edges_from(list(self._graph.edges()))

        # Build lookup tables for import resolution
        path_set = set(self._parsed_files.keys())
        # stem_map: "calculator" → "python_pkg/calculator.py"
        stem_map: dict[str, str] = {}
        for p in path_set:
            stem = Path(p).stem.lower()
            stem_map[stem] = p

        for path, parsed in self._parsed_files.items():
            for imp in parsed.imports:
                target = self._resolve_import(
                    imp.module_path, path, path_set, stem_map, parsed.file_info.language
                )
                if target:
                    # Aggregate imported_names on parallel edges
                    if self._graph.has_edge(path, target):
                        existing = self._graph[path][target].get("imported_names", [])
                        merged = list(set(existing + imp.imported_names))
                        self._graph[path][target]["imported_names"] = merged
                    else:
                        self._graph.add_edge(
                            path,
                            target,
                            imported_names=list(imp.imported_names),
                        )

        self._built = True
        log.info(
            "Graph built",
            nodes=self._graph.number_of_nodes(),
            edges=self._graph.number_of_edges(),
        )
        return self._graph

    def graph(self) -> nx.DiGraph:
        """Return the graph (building it first if necessary)."""
        if not self._built:
            self.build()
        return self._graph

    # ------------------------------------------------------------------
    # Graph metrics
    # ------------------------------------------------------------------

    def strongly_connected_components(self) -> list[frozenset[str]]:
        """Return SCCs as a list of frozensets. SCCs of size > 1 are circular deps."""
        return [frozenset(scc) for scc in nx.strongly_connected_components(self.graph())]

    def betweenness_centrality(self) -> dict[str, float]:
        """Return betweenness centrality. High value → bridge file.

        Approximated with k=min(500, n) samples for large graphs.
        """
        g = self.graph()
        n = g.number_of_nodes()
        if n == 0:
            return {}
        if n > _LARGE_REPO_THRESHOLD:
            k = min(500, n)
            return nx.betweenness_centrality(g, k=k, normalized=True)
        return nx.betweenness_centrality(g, normalized=True)

    def community_detection(self) -> dict[str, int]:
        """Assign a community ID to each node using the Louvain algorithm.

        Returns dict[path, community_id].
        """
        g = self.graph()
        if g.number_of_nodes() == 0:
            return {}
        try:
            communities = nx.community.louvain_communities(g.to_undirected(), seed=42)
            result: dict[str, int] = {}
            for community_id, members in enumerate(communities):
                for node in members:
                    result[node] = community_id
            return result
        except Exception as exc:
            log.warning("Community detection failed", error=str(exc))
            return {node: 0 for node in g.nodes()}

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialize the graph to a JSON-compatible dict (node-link format)."""
        return nx.node_link_data(self.graph())

    async def persist(self, db_path: Path, repo_id: str) -> None:
        """Persist the graph to an SQLite database (lightweight Phase-2 schema).

        Phase 4 will replace this with the full SQLAlchemy/Alembic schema.
        """
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

            # Nodes
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

            # Edges
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

    def _resolve_import(
        self,
        module_path: str,
        importer_path: str,
        path_set: set[str],
        stem_map: dict[str, str],
        language: str,
    ) -> str | None:
        """Best-effort resolve of an import to a known file path."""
        if not module_path:
            return None

        importer_dir = Path(importer_path).parent

        # --- Python ---
        if language == "python":
            # Relative import: ".sibling" or "..parent.module"
            if module_path.startswith("."):
                dots = len(module_path) - len(module_path.lstrip("."))
                rest = module_path[dots:].replace(".", "/")
                base = importer_dir
                for _ in range(dots - 1):
                    base = base.parent
                candidates = [
                    (base / rest).with_suffix(".py").as_posix() if rest else None,
                    (base / rest / "__init__.py").as_posix() if rest else None,
                ]
                for c in candidates:
                    if c and c in path_set:
                        return c
                return None
            # Absolute import: "python_pkg.calculator" → "python_pkg/calculator.py"
            dotted = module_path.replace(".", "/")
            candidates = [
                f"{dotted}.py",
                f"{dotted}/__init__.py",
            ]
            for c in candidates:
                if c in path_set:
                    return c
            # Stem-only fallback
            stem = module_path.split(".")[-1].lower()
            return stem_map.get(stem)

        # --- TypeScript / JavaScript ---
        if language in ("typescript", "javascript"):
            if module_path.startswith("."):
                base = importer_dir / module_path
                for ext in (".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"):
                    candidate = Path(str(base) + ext).as_posix()
                    if candidate in path_set:
                        return candidate
                    candidate = (
                        base.with_suffix(ext).as_posix()
                        if not ext.startswith("/")
                        else (base / "index.ts").as_posix()
                    )
                    if candidate in path_set:
                        return candidate
            # External npm package
            external_key = f"external:{module_path}"
            if external_key not in self._graph.nodes:
                self._graph.add_node(
                    external_key, language="external", symbol_count=0, has_error=False
                )
            return external_key

        # --- Go ---
        if language == "go":
            # Last segment of the import path is the package name
            stem = module_path.rsplit("/", 1)[-1].lower()
            return stem_map.get(stem)

        # --- Generic fallback: stem matching ---
        stem = Path(module_path).stem.lower()
        return stem_map.get(stem)

    # ------------------------------------------------------------------
    # Co-change edges (Phase 5.5)
    # ------------------------------------------------------------------

    def add_co_change_edges(self, git_meta_map: dict, min_count: int = 3) -> int:
        """Add co_changes edges from git metadata. Returns count of edges added.

        These DO NOT affect PageRank — filter them out before computing.
        """
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

                # Don't add if an import edge already exists
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
        # Remove existing co_changes edges involving updated files
        edges_to_remove = []
        for u, v, data in self._graph.edges(data=True):
            if data.get("edge_type") == "co_changes" and (u in updated_meta or v in updated_meta):
                edges_to_remove.append((u, v))
        self._graph.remove_edges_from(edges_to_remove)

        # Re-add co_changes edges
        self.add_co_change_edges(updated_meta, min_count)

    def pagerank(self, alpha: float = 0.85) -> dict[str, float]:
        """Return PageRank scores for each node.

        High PageRank → file is imported by many others → high documentation priority.
        Co-change edges are filtered out before computing PageRank.
        """
        g = self.graph()
        if g.number_of_nodes() == 0:
            return {}

        # Create a filtered view excluding co_changes edges
        filtered = nx.DiGraph()
        filtered.add_nodes_from(g.nodes(data=True))
        for u, v, data in g.edges(data=True):
            if data.get("edge_type") != "co_changes":
                filtered.add_edge(u, v, **data)

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
