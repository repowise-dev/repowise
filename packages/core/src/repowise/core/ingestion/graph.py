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
import shlex
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

    def __init__(self, repo_path: Path | str | None = None) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._parsed_files: dict[str, ParsedFile] = {}  # path → ParsedFile
        self._built = False
        self._repo_path: Path | None = Path(repo_path) if repo_path else None
        self._compile_commands_cache: dict[str, dict] | None = None

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

    def _load_compile_commands(self) -> dict[str, dict] | None:
        """Load and cache compile_commands.json if present in the repo.

        Returns dict[source_file_relpath] → command entry, or None if not found.
        """
        if self._compile_commands_cache is not None:
            return self._compile_commands_cache
        if not self._repo_path:
            return None
        for candidate in [
            self._repo_path / "compile_commands.json",
            self._repo_path / "build" / "compile_commands.json",
        ]:
            if candidate.exists():
                try:
                    with open(candidate) as f:
                        commands = json.load(f)
                    result: dict[str, dict] = {}
                    for entry in commands:
                        file_path = Path(entry.get("file", ""))
                        if file_path.is_absolute():
                            try:
                                file_rel = file_path.relative_to(self._repo_path)
                            except ValueError:
                                continue
                        else:
                            file_rel = file_path
                        result[file_rel.as_posix()] = entry
                    if result:
                        self._compile_commands_cache = result
                        log.info(
                            "Loaded compile_commands.json",
                            path=str(candidate),
                            entries=len(self._compile_commands_cache),
                        )
                        return self._compile_commands_cache
                    # No valid entries — try next candidate
                    log.debug("compile_commands.json had no resolvable entries", path=str(candidate))
                except Exception as exc:
                    log.debug("Failed to load compile_commands.json", error=str(exc))
        return None

    def _extract_include_dirs(self, source_file: str) -> list[str]:
        """Return absolute include directories for source_file from compile_commands.json."""
        commands = self._load_compile_commands()
        if not commands or source_file not in commands:
            return []
        entry = commands[source_file]
        cmd_dir = Path(entry.get("directory", str(self._repo_path or "")))
        # compile_commands.json entries use either "arguments" (pre-split array)
        # or "command" (shell-quoted string) — check arguments first
        if "arguments" in entry:
            tokens = list(entry["arguments"])
        else:
            command = entry.get("command", "")
            try:
                tokens = shlex.split(command)
            except ValueError:
                return []
        include_dirs: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("-I", "-isystem", "-iquote"):
                if i + 1 < len(tokens):
                    include_dirs.append(tokens[i + 1])
                    i += 2
                else:
                    i += 1
            elif tok.startswith("-I") and len(tok) > 2:
                include_dirs.append(tok[2:])
                i += 1
            elif tok.startswith("-isystem") and len(tok) > 8:
                include_dirs.append(tok[8:])
                i += 1
            else:
                i += 1
        result: list[str] = []
        for d in include_dirs:
            p = Path(d)
            if not p.is_absolute():
                p = cmd_dir / p
            result.append(str(p.resolve()))
        return result

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

        # --- C / C++ ---
        if language in ("cpp", "c"):
            repo_root = self._repo_path.resolve() if self._repo_path else None
            # 1. Try compile_commands.json include paths
            for inc_dir in self._extract_include_dirs(importer_path):
                candidate = (Path(inc_dir) / module_path).resolve()
                if repo_root:
                    try:
                        rel = candidate.relative_to(repo_root).as_posix()
                        if rel in path_set:
                            return rel
                    except ValueError:
                        pass
            # 2. Try relative to the importer's directory
            if repo_root:
                try:
                    rel = (importer_dir / module_path).resolve().relative_to(repo_root).as_posix()
                    if rel in path_set:
                        return rel
                except ValueError:
                    pass
            # 3. Stem-matching fallback
            stem = Path(module_path).stem.lower()
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

    # ------------------------------------------------------------------
    # Dynamic-hint edges
    # ------------------------------------------------------------------

    def add_dynamic_edges(self, edges: list) -> None:
        """Add dynamic-hint edges to the graph. Each edge is a DynamicEdge."""
        for e in edges:
            if e.source not in self._graph:
                continue
            if e.target not in self._graph:
                # add a stub node so dead-code analysis sees it as reachable
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

        Detects common patterns (conftest fixtures, Django settings/admin/urls,
        FastAPI include_router, Flask register_blueprint) and creates directed
        edges with ``edge_type="framework"``.  These edges participate in
        PageRank (they represent real runtime dependencies).

        Returns the number of edges added.
        """
        count = 0
        path_set = set(self._parsed_files.keys())

        # Always run: pytest conftest detection
        count += self._add_conftest_edges(path_set)

        stack_lower = {s.lower() for s in (tech_stack or [])}

        if "django" in stack_lower:
            count += self._add_django_edges(path_set)
        if "fastapi" in stack_lower or "starlette" in stack_lower:
            count += self._add_fastapi_edges(path_set)
        if "flask" in stack_lower:
            count += self._add_flask_edges(path_set)

        if count:
            log.info("Framework edges added", count=count)
        return count

    def _add_edge_if_new(self, source: str, target: str) -> bool:
        """Add a framework edge if no edge already exists. Returns True if added."""
        if source == target:
            return False
        if self._graph.has_edge(source, target):
            return False
        self._graph.add_edge(source, target, edge_type="framework", imported_names=[])
        return True

    def _add_conftest_edges(self, path_set: set[str]) -> int:
        """conftest.py → test files in the same or child directories."""
        count = 0
        conftest_paths = [p for p in path_set if Path(p).name == "conftest.py"]

        for conf in conftest_paths:
            conf_dir = Path(conf).parent.as_posix()
            prefix = f"{conf_dir}/" if conf_dir != "." else ""
            for p in path_set:
                if p == conf:
                    continue
                node = self._graph.nodes.get(p, {})
                if not node.get("is_test", False):
                    continue
                # Test file must be in the same or a child directory
                if (
                    p.startswith(prefix) or (prefix == "" and "/" not in p)
                ) and self._add_edge_if_new(p, conf):
                    count += 1
        return count

    def _add_django_edges(self, path_set: set[str]) -> int:
        """Django conventions: admin→models, urls→views in the same directory."""
        count = 0
        by_dir: dict[str, dict[str, str]] = {}  # dir → {stem: path}
        for p in path_set:
            pp = Path(p)
            d = pp.parent.as_posix()
            by_dir.setdefault(d, {})[pp.stem] = p

        for _d, stems in by_dir.items():
            # admin.py → models.py
            if (
                "admin" in stems
                and "models" in stems
                and self._add_edge_if_new(stems["admin"], stems["models"])
            ):
                count += 1
            # urls.py → views.py
            if (
                "urls" in stems
                and "views" in stems
                and self._add_edge_if_new(stems["urls"], stems["views"])
            ):
                count += 1
            # forms.py → models.py
            if (
                "forms" in stems
                and "models" in stems
                and self._add_edge_if_new(stems["forms"], stems["models"])
            ):
                count += 1
            # serializers.py → models.py
            if (
                "serializers" in stems
                and "models" in stems
                and self._add_edge_if_new(stems["serializers"], stems["models"])
            ):
                count += 1
        return count

    def _add_fastapi_edges(self, path_set: set[str]) -> int:
        """Detect include_router() calls and link app files to router modules."""
        import re

        count = 0
        # Build a map from imported variable names to source file paths
        var_to_file: dict[str, str] = {}
        stem_map = {Path(p).stem.lower(): p for p in path_set}
        for path, parsed in self._parsed_files.items():
            for imp in parsed.imports:
                for name in imp.imported_names:
                    if name.lower().endswith("router") or name.lower().endswith("app"):
                        resolved = self._resolve_import(
                            imp.module_path,
                            path,
                            path_set,
                            stem_map,
                            parsed.file_info.language,
                        )
                        if resolved and resolved in path_set:
                            var_to_file[name] = resolved

        router_re = re.compile(r"(?:include_router|add_api_route)\s*\(\s*(\w+)")
        for path, parsed in self._parsed_files.items():
            if parsed.file_info.language != "python":
                continue
            try:
                source = Path(parsed.file_info.abs_path).read_text(errors="ignore")
            except Exception:
                continue
            for match in router_re.finditer(source):
                var_name = match.group(1)
                target = var_to_file.get(var_name)
                if target and target in path_set and self._add_edge_if_new(path, target):
                    count += 1
        return count

    def _add_flask_edges(self, path_set: set[str]) -> int:
        """Detect register_blueprint() calls and link app files to blueprint modules."""
        import re

        count = 0
        var_to_file: dict[str, str] = {}
        stem_map = {Path(p).stem.lower(): p for p in path_set}
        for path, parsed in self._parsed_files.items():
            for imp in parsed.imports:
                for name in imp.imported_names:
                    if "blueprint" in name.lower() or name.lower().endswith("bp"):
                        resolved = self._resolve_import(
                            imp.module_path,
                            path,
                            path_set,
                            stem_map,
                            parsed.file_info.language,
                        )
                        if resolved and resolved in path_set:
                            var_to_file[name] = resolved

        bp_re = re.compile(r"register_blueprint\s*\(\s*(\w+)")
        for path, parsed in self._parsed_files.items():
            if parsed.file_info.language != "python":
                continue
            try:
                source = Path(parsed.file_info.abs_path).read_text(errors="ignore")
            except Exception:
                continue
            for match in bp_re.finditer(source):
                var_name = match.group(1)
                target = var_to_file.get(var_name)
                if target and target in path_set and self._add_edge_if_new(path, target):
                    count += 1
        return count

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
