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
import shlex
from pathlib import Path
from typing import Any

import networkx as nx
import structlog

from .models import ParsedFile

log = structlog.get_logger(__name__)

_LARGE_REPO_THRESHOLD = 30_000  # nodes — above this, algorithms are expensive

# Path segments that mark a file as low-value for stem-based import resolution.
# Files under these directories lose stem-collision tiebreaks against equivalents
# in the canonical source tree (e.g. a `flask.py` test fixture will never beat
# `src/flask/__init__.py` for the import-stem "flask"). The list is intentionally
# language-agnostic — it captures the universal convention that fixture, example,
# and script trees shadow rather than replace library code.
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
    The ordering is deliberately language-agnostic so the same logic governs
    Python, Go, C/C++, and the generic fallback in :meth:`_resolve_import`.

    Fields, in priority order:

    1. **Parent-directory match.** A file whose parent directory equals the
       stem is almost always the canonical home for that name across every
       package layout we care about — ``src/flask/__init__.py`` for stem
       ``flask``, ``pkg/foo/foo.go`` for stem ``foo``, ``include/json/json.h``
       for stem ``json``. Strongest single signal we have.
    2. **Low-value path.** Files under fixture/example/script/doc trees lose
       to equivalents in the source tree. This catches the failure mode
       where a test fixture named identically to the package (e.g.
       ``tests/.../<pkg>.py``) would otherwise win the stem-collision
       tiebreak and inflate its PageRank by absorbing the entire library's
       in-edges.
    3. **Path depth.** Canonical package roots live shallow; deep nesting
       usually means a vendored copy or a sub-fixture.
    4. **Lexicographic path.** Deterministic tiebreak so resolution is
       independent of dict iteration order — critical for reproducible
       graphs across re-indexes and platforms.
    """
    path_obj = Path(path)
    parts = path_obj.parts
    if path_obj.name == "__init__.py":
        # Registered under parent dir name — parent-matching by construction.
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
        self._compile_commands_cache: dict[str, dict] | None = None
        self._tsconfig_resolver: Any | None = None  # TsconfigResolver (lazy import)
        self._go_module_path: str | None = self._read_go_module_path()

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
        """Resolve imports and calls, add edges. Returns the finalized graph.

        Idempotent: can be called multiple times; re-resolves edges each time.
        Preserves symbol nodes and structural edges (defines, has_method)
        while rebuilding import and call edges.
        """
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

        # --- Phase 1: Resolve file-level imports ---
        import_targets: dict[str, set[str]] = {}  # file → set of imported files

        for path, parsed in self._parsed_files.items():
            file_imports: set[str] = set()
            for imp in parsed.imports:
                target = self._resolve_import(
                    imp.module_path, path, path_set, stem_map, parsed.file_info.language
                )
                if target:
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

        # --- Phase 2: Resolve symbol-level calls ---
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
                # Only add edge if both nodes exist in graph
                if rc.caller_id in self._graph and rc.callee_id in self._graph:
                    # Avoid duplicate call edges between same pair
                    if not self._graph.has_edge(rc.caller_id, rc.callee_id):
                        self._graph.add_edge(
                            rc.caller_id,
                            rc.callee_id,
                            edge_type="calls",
                            confidence=rc.confidence,
                        )
                        total_resolved += 1
                    else:
                        # Update confidence if this resolution is higher
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
        """Return SCCs as a list of frozensets. SCCs of size > 1 are circular deps.

        Operates on the file-level subgraph only.
        """
        return [frozenset(scc) for scc in nx.strongly_connected_components(self.file_subgraph())]

    def betweenness_centrality(self) -> dict[str, float]:
        """Return betweenness centrality for file nodes. High value → bridge file.

        Approximated with k=min(500, n) samples for large graphs.
        Operates on the file-level subgraph only.
        """
        g = self.file_subgraph()
        n = g.number_of_nodes()
        if n == 0:
            return {}
        if n > _LARGE_REPO_THRESHOLD:
            k = min(500, n)
            return nx.betweenness_centrality(g, k=k, normalized=True)
        return nx.betweenness_centrality(g, normalized=True)

    def community_detection(self) -> dict[str, int]:
        """Assign a community ID to each file node using the Louvain algorithm.

        Returns dict[path, community_id]. Operates on file-level subgraph only.
        """
        g = self.file_subgraph()
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
                    log.debug(
                        "compile_commands.json had no resolvable entries", path=str(candidate)
                    )
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

    def _build_stem_map(self, path_set: set[str]) -> dict[str, list[str]]:
        """Map import-stems to candidate file paths, sorted best-first.

        For Python ``__init__.py`` files the stem is the *parent directory
        name*, since ``import flask`` resolves to ``src/flask/__init__.py``
        and not to a file with literal stem ``__init__``. For every other
        file the stem is the filename without extension. The same map is
        consulted by Python, Go, C/C++, and the generic fallback in
        :meth:`_resolve_import` — keeping all collision logic in one place
        is what makes the resolver deterministic across languages.

        On stem collisions (test fixtures, vendored copies, deep examples)
        candidates are sorted by :func:`_stem_priority` so callers can take
        ``candidates[0]`` and get the canonical resolution. The fix that
        prevents test-fixture-named-like-the-package PageRank inflation
        lives here, not in any per-directory exclusion list.

        Complexity: O(N) build, plus O(k log k) per bucket of size k. Total
        worst case O(N log N) when one stem dominates; in practice O(N).
        """
        buckets: dict[str, list[str]] = {}
        for p in path_set:
            path_obj = Path(p)
            if path_obj.name == "__init__.py":
                parent = path_obj.parent.name
                if not parent:
                    # Repo-root __init__.py — no meaningful key. Skip rather
                    # than register under the empty stem.
                    continue
                stem = parent.lower()
            else:
                stem = path_obj.stem.lower()
            buckets.setdefault(stem, []).append(p)

        for stem, paths in buckets.items():
            paths.sort(key=lambda candidate: _stem_priority(candidate, stem))
        return buckets

    @staticmethod
    def _stem_lookup(stem_map: dict[str, list[str]], stem: str) -> str | None:
        """Return the highest-priority path for ``stem``, or None."""
        candidates = stem_map.get(stem)
        return candidates[0] if candidates else None

    def _resolve_import(
        self,
        module_path: str,
        importer_path: str,
        path_set: set[str],
        stem_map: dict[str, list[str]],
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
            # Try the obvious filesystem layouts in order. Modern Python
            # packaging conventions place the package under "src/", so we
            # check that prefix too — non-existent candidates are filtered
            # by the path_set membership check, so adding more candidates
            # is free of regressions.
            dotted = module_path.replace(".", "/")
            candidates = [
                f"{dotted}.py",
                f"{dotted}/__init__.py",
                f"src/{dotted}.py",
                f"src/{dotted}/__init__.py",
            ]
            for c in candidates:
                if c in path_set:
                    return c
            # Stem-only fallback — uses the deterministic priority from
            # _build_stem_map so test fixtures named like the package
            # cannot win against the canonical source file.
            stem = module_path.split(".")[-1].lower()
            return self._stem_lookup(stem_map, stem)

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
                return None

            # Non-relative: try tsconfig path-alias resolution first.
            if self._tsconfig_resolver is not None:
                importer_abs = (
                    str(self._repo_path / importer_path) if self._repo_path else importer_path
                )
                alias_resolved = self._tsconfig_resolver.resolve(module_path, importer_abs)
                if alias_resolved is not None:
                    return alias_resolved

            # Fallback: external npm package.
            external_key = f"external:{module_path}"
            if external_key not in self._graph.nodes:
                self._graph.add_node(
                    external_key, language="external", symbol_count=0, has_error=False
                )
            return external_key

        # --- Go ---
        if language == "go":
            return self._resolve_go_import(module_path, path_set, stem_map)

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
            return self._stem_lookup(stem_map, stem)

        # --- Rust ---
        if language == "rust":
            return self._resolve_rust_import(module_path, importer_path, path_set, stem_map)

        # --- Generic fallback: stem matching ---
        stem = Path(module_path).stem.lower()
        return self._stem_lookup(stem_map, stem)

    # ------------------------------------------------------------------
    # Go module resolution
    # ------------------------------------------------------------------

    def _read_go_module_path(self) -> str | None:
        """Read the ``module`` directive from ``go.mod``, if present."""
        if self._repo_path is None:
            return None
        go_mod = self._repo_path / "go.mod"
        if not go_mod.is_file():
            return None
        try:
            for line in go_mod.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("module "):
                    return line.split(None, 1)[1].strip()
        except Exception:
            pass
        return None

    def _resolve_go_import(
        self,
        module_path: str,
        path_set: set[str],
        stem_map: dict[str, str],
    ) -> str | None:
        """Resolve a Go import path.

        If ``go.mod`` declares ``module github.com/org/repo``, then an
        import ``github.com/org/repo/pkg/util`` maps to ``pkg/util/*.go``
        in the repository.  Unmatched imports are classified as external.
        """
        # If we know the module path and the import starts with it,
        # strip the prefix to get the repo-relative package dir.
        if self._go_module_path and module_path.startswith(self._go_module_path):
            suffix = module_path[len(self._go_module_path) :]
            rel_dir = suffix.lstrip("/")
            # Find any Go file in that directory
            for p in path_set:
                if p.endswith(".go"):
                    p_dir = str(Path(p).parent.as_posix())
                    if p_dir == rel_dir or p_dir.endswith(f"/{rel_dir}"):
                        return p
            # Try stem matching as fallback for the package name
            pkg_name = rel_dir.rsplit("/", 1)[-1].lower() if rel_dir else ""
            if pkg_name:
                result = self._stem_lookup(stem_map, pkg_name)
                if result:
                    return result

        # No go.mod match — fall back to stem matching on the last segment
        stem = module_path.rsplit("/", 1)[-1].lower()
        result = self._stem_lookup(stem_map, stem)
        if result:
            return result

        # External package
        external_key = f"external:{module_path}"
        if external_key not in self._graph.nodes:
            self._graph.add_node(
                external_key, language="external", symbol_count=0, has_error=False
            )
        return external_key

    # ------------------------------------------------------------------
    # Rust import resolution
    # ------------------------------------------------------------------

    def _resolve_rust_import(
        self,
        module_path: str,
        importer_path: str,
        path_set: set[str],
        stem_map: dict[str, str],
    ) -> str | None:
        """Resolve a Rust ``use`` path to a repo-relative file.

        Handles ``crate::``, ``self::``, ``super::`` prefixes by mapping
        them to filesystem paths, then probing for ``<name>.rs`` or
        ``<name>/mod.rs``.  External crates are classified as ``external:``
        nodes.
        """
        parts = module_path.split("::")
        if not parts:
            return None

        prefix = parts[0]

        # --- crate:: — resolve from the crate root ---
        if prefix == "crate":
            crate_root = self._find_rust_crate_root(importer_path)
            return self._probe_rust_path(crate_root, parts[1:], path_set)

        # --- self:: — resolve from the current module's directory ---
        if prefix == "self":
            importer_dir = str(Path(importer_path).parent.as_posix())
            return self._probe_rust_path(importer_dir, parts[1:], path_set)

        # --- super:: — resolve from the parent directory ---
        if prefix == "super":
            parent_dir = str(Path(importer_path).parent.parent.as_posix())
            return self._probe_rust_path(parent_dir, parts[1:], path_set)

        # --- External crate (no prefix or unknown crate name) ---
        # Check if it might be a local module at the crate root first
        crate_root = self._find_rust_crate_root(importer_path)
        resolved = self._probe_rust_path(crate_root, parts, path_set)
        if resolved is not None:
            return resolved

        # External crate
        external_key = f"external:{module_path}"
        if external_key not in self._graph.nodes:
            self._graph.add_node(
                external_key, language="external", symbol_count=0, has_error=False
            )
        return external_key

    def _find_rust_crate_root(self, importer_path: str) -> str:
        """Find the ``src/`` directory containing the importer (Rust crate root).

        Walks up from the importer looking for ``lib.rs`` or ``main.rs``
        siblings, returning the directory containing them.  Falls back
        to the nearest ``src/`` directory.
        """
        parts = Path(importer_path).parts
        for i in range(len(parts) - 1, -1, -1):
            candidate_dir = Path(*parts[:i]) if i > 0 else Path(".")
            for root_file in ("lib.rs", "main.rs"):
                root_path = (candidate_dir / root_file).as_posix()
                if root_path in self._parsed_files:
                    return candidate_dir.as_posix()
            if parts[i] == "src" and i > 0:
                return candidate_dir.as_posix()
        return Path(importer_path).parent.as_posix()

    @staticmethod
    def _probe_rust_path(
        base_dir: str,
        path_parts: list[str],
        path_set: set[str],
    ) -> str | None:
        """Probe the file system for a Rust module path.

        For ``[\"models\", \"Calculator\"]`` from base ``src/``, tries:
        - ``src/models.rs`` (module file)
        - ``src/models/mod.rs`` (directory module)
        Then recurses into deeper segments.  The last segment is often
        a symbol name (struct/fn), so we try without it too.
        """
        if not path_parts:
            return None

        base = Path(base_dir)

        # Try progressively deeper path segments — the last N segments
        # may be symbol names rather than module names.
        for depth in range(len(path_parts), 0, -1):
            module_parts = path_parts[:depth]
            # Build the path: base / part1 / part2 / ... / partN-1
            module_dir = base
            for p in module_parts[:-1]:
                module_dir = module_dir / p

            last = module_parts[-1]
            # Try <dir>/<last>.rs
            candidate = (module_dir / f"{last}.rs").as_posix()
            if candidate in path_set:
                return candidate
            # Try <dir>/<last>/mod.rs
            candidate = (module_dir / last / "mod.rs").as_posix()
            if candidate in path_set:
                return candidate

        return None

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

    def file_subgraph(self) -> nx.DiGraph:
        """Return a subgraph containing only file-level nodes and import edges.

        Used for PageRank, betweenness, and other file-level metrics that
        should not be affected by symbol-level nodes.
        """
        g = self.graph()
        file_nodes = [
            n
            for n, d in g.nodes(data=True)
            if d.get("node_type", "file") in ("file", "external")
        ]
        sub = g.subgraph(file_nodes).copy()
        # Remove non-import edges (co_changes, framework, dynamic)
        edges_to_remove = [
            (u, v) for u, v, d in sub.edges(data=True) if d.get("edge_type") in ("co_changes",)
        ]
        sub.remove_edges_from(edges_to_remove)
        return sub

    def pagerank(self, alpha: float = 0.85) -> dict[str, float]:
        """Return PageRank scores for file nodes only.

        High PageRank → file is imported by many others → high documentation priority.
        Operates on the file-level subgraph (excludes symbol nodes and co-change edges).
        """
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
