"""GraphBuilder — constructs the directed dependency graph from ParsedFiles.

This module holds the structural core (node/edge construction, import
resolution driver, lifecycle). Metrics, edge augmentation, resolution passes,
and serialisation are provided by mixins in sibling modules to keep every
file under the project's 400-line ceiling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import networkx as nx
import structlog

from ..models import ParsedFile
from ..resolvers import ResolverContext, resolve_import
from ..resolvers.go import read_go_module_path, read_go_modules
from ..type_ref_resolution import resolve_type_refs
from ._edges import EdgesMixin
from ._metrics import MetricsMixin
from ._rehydrate import RehydrateMixin
from ._resolvers import ResolveMixin
from ._serialize import SerializeMixin
from ._stem import build_stem_map

log = structlog.get_logger(__name__)


class GraphBuilder(MetricsMixin, ResolveMixin, EdgesMixin, SerializeMixin, RehydrateMixin):
    """Build a dependency graph from a collection of ParsedFile objects.

    Usage::

        builder = GraphBuilder()
        for parsed in parsed_files:
            builder.add_file(parsed)
        graph = builder.build()
        pr = builder.pagerank()
    """

    def __init__(
        self,
        repo_path: Path | str | None = None,
        *,
        exclude_patterns: list[str] | None = None,
        centrality_cache_dir: Path | str | None = None,
        include_submodules: bool = False,
        include_nested_repos: bool = False,
    ) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._parsed_files: dict[str, ParsedFile] = {}  # path → ParsedFile
        self._built = False
        # Resolver-built DotNetProjectIndex, stashed by build() for the
        # dynamic-hints phase to reuse (see build()).
        self.dotnet_index: Any | None = None
        self._repo_path: Path | None = Path(repo_path) if repo_path else None
        # Mirrors the traverser flags: when submodules or nested repos are
        # indexed, resolver filesystem scans must not prune them (both are
        # ``.git``-bearing subdirs to fs_walk's prune_nested_git).
        self._prune_nested_git: bool = not (include_submodules or include_nested_repos)
        self._tsconfig_resolver: Any | None = None  # TsconfigResolver (lazy import)
        # Optional structure-keyed disk cache for betweenness (the most
        # expensive metric kernel). Opt-in via *centrality_cache_dir* — the
        # ingest paths pass ``<repo>/.repowise``; ad-hoc builders are unchanged.
        self._centrality_cache: Any | None = None
        if centrality_cache_dir is not None:
            try:
                from ._centrality_cache import CentralityCache

                self._centrality_cache = CentralityCache(centrality_cache_dir)
            except Exception:
                self._centrality_cache = None

        import pathspec

        self._exclude = pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns or [])

        # Community / flow / metric caches (invalidated on build)
        self._community_cache: dict[str, int] | None = None
        self._symbol_community_cache: dict[str, int] | None = None
        self._community_info_cache: dict[int, Any] | None = None
        self._community_algo: str = ""
        self._pagerank_cache: dict[str, float] | None = None
        self._betweenness_cache: dict[str, float] | None = None
        self._in_degree_cache: dict[str, int] | None = None
        self._out_degree_cache: dict[str, int] | None = None
        self._symbol_pagerank_cache: dict[str, float] | None = None
        self._symbol_betweenness_cache: dict[str, float] | None = None
        self._execution_flow_cache: Any | None = None
        # Filtered-subgraph caches — rebuilt lazily; guarded by a lock because
        # the init pipeline computes metrics concurrently (asyncio.to_thread).
        import threading

        self._subgraph_lock = threading.Lock()
        self._file_subgraph_cache: nx.DiGraph | None = None
        self._symbol_subgraph_cache: nx.DiGraph | None = None
        # Shared import-name maps (built once per build(), injected into the
        # call + heritage resolvers; reset whenever files change).
        self._import_name_maps: Any | None = None

    def set_tsconfig_resolver(self, resolver: Any) -> None:
        """Attach a :class:`TsconfigResolver` for TS/JS path-alias resolution."""
        self._tsconfig_resolver = resolver

    def _invalidate_metric_caches(self) -> None:
        """Clear every cached metric (called on build + add_file)."""
        self._community_cache = None
        self._symbol_community_cache = None
        self._community_info_cache = None
        self._community_algo = ""
        self._pagerank_cache = None
        self._betweenness_cache = None
        self._in_degree_cache = None
        self._out_degree_cache = None
        self._symbol_pagerank_cache = None
        self._symbol_betweenness_cache = None
        self._execution_flow_cache = None
        self._file_subgraph_cache = None
        self._symbol_subgraph_cache = None
        self._import_name_maps = None

    def _invalidate_subgraph_caches(self) -> None:
        """Clear only the filtered-subgraph caches.

        Called by graph mutations that historically did NOT clear the metric
        caches (co-change edge refresh, framework edges) — those keep their
        existing semantics, but a cached subgraph must never go stale
        structurally.
        """
        self._file_subgraph_cache = None
        self._symbol_subgraph_cache = None

    def release_graph(self) -> None:
        """Drop the in-memory NetworkX object after metrics are materialized.

        For large-repo scale work: once file-level metrics have been loaded
        from SQL via :meth:`load_metrics_from_sql`, callers that no longer need
        graph traversal (e.g. the fast-mode pipeline, which generates no docs)
        can release the structural graph to free memory. Metric reads continue
        to be served from the loaded caches.

        ``_built`` stays True so a subsequent ``graph()`` call returns the
        empty graph rather than silently (and expensively) rebuilding the
        structure we deliberately dropped.
        """
        self._graph = nx.DiGraph()
        self._built = True
        self._invalidate_subgraph_caches()  # they hold the old structure — free it

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
            docstring=parsed.docstring,
            # Same-file references (Python): names used intra-module in a
            # non-call/non-import position. Rescues them in the unused-export
            # pass. An immutable frozenset (never mutated post-stamp, unlike
            # ``local_type_uses`` which the type-ref phase ``.update()``s).
            local_refs=parsed.local_refs,
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
                decorators=sym.decorators,
                is_exported_symbol=sym.is_exported_symbol,
                docstring=sym.docstring,
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

        # --- Synthetic module-level symbol for top-level calls ---
        module_sym_id = f"{path}::__module__"
        self._graph.add_node(
            module_sym_id,
            node_type="symbol",
            kind="module",
            name="__module__",
            file_path=path,
            start_line=0,
            end_line=0,
            visibility="private",
            language=parsed.file_info.language,
            docstring=parsed.docstring,
        )
        self._graph.add_edge(path, module_sym_id, edge_type="defines")

    def build(self, progress: Any | None = None) -> nx.DiGraph:
        """Resolve imports and calls, add edges. Returns the finalized graph.

        *progress* is an optional ``ProgressCallback`` (duck-typed). When
        provided, sub-phase events ``graph.imports`` / ``graph.heritage`` /
        ``graph.calls`` are emitted so the CLI can surface per-file progress
        instead of a single opaque "0/1" bar over the whole build.
        """
        self._invalidate_metric_caches()

        # Clear import/call edges but keep structural edges (defines, has_method)
        edges_to_remove = [
            (u, v)
            for u, v, d in self._graph.edges(data=True)
            if d.get("edge_type") not in ("defines", "has_method")
        ]
        self._graph.remove_edges_from(edges_to_remove)

        # Build lookup tables for import resolution
        path_set = set(self._parsed_files.keys())
        stem_map = build_stem_map(path_set)

        # Construct resolver context
        go_modules = read_go_modules(self._repo_path, prune_nested_git=self._prune_nested_git)
        ctx = ResolverContext(
            path_set=path_set,
            stem_map=stem_map,
            graph=self._graph,
            repo_path=self._repo_path,
            prune_nested_git=self._prune_nested_git,
            tsconfig_resolver=self._tsconfig_resolver,
            go_module_path=(
                go_modules[-1][1] if go_modules else read_go_module_path(self._repo_path)
            ),
            go_modules=go_modules,
            has_sfc_files=any(p.endswith((".vue", ".svelte", ".astro")) for p in path_set),
            parsed_files=self._parsed_files,
        )

        # --- Phase 1 prelude: language-specific warmups ---
        # Some languages need an expensive one-time index built before any
        # per-file import resolution can run. Doing it here, under a dedicated
        # phase event, keeps the cost out of ``graph.imports`` and surfaces
        # a meaningful progress label instead of a stuck per-file bar.
        from ..graph_warmups import run_warmups

        run_warmups(self._parsed_files, ctx, progress=progress)

        # --- Phase 1: Resolve file-level imports ---
        import_targets: dict[str, set[str]] = {}  # file → set of imported files

        # Per-language import-resolution timing — surfaces which language is
        # actually dominating the import loop on multi-language repos
        # (audit #30). Persisted to state.json for after-the-fact analysis.
        import time as _t

        lang_import_time: dict[str, float] = {}

        if progress:
            progress.on_phase_start("graph.imports", len(self._parsed_files))
        for path, parsed in self._parsed_files.items():
            _lang = parsed.file_info.language
            _t0 = _t.monotonic()
            file_imports: set[str] = set()
            for imp in parsed.imports:
                # Go and JVM imports name a package *directory*; fan the edge
                # out to every file in the resolved package so sibling files
                # share importers (otherwise they look unreachable).
                if _lang == "go":
                    from ..resolvers.go import resolve_go_import_all

                    targets = resolve_go_import_all(imp.module_path, path, ctx)
                elif _lang == "java":
                    from ..resolvers.java import resolve_java_import_all

                    targets = resolve_java_import_all(imp.module_path, path, ctx)
                elif _lang == "kotlin":
                    from ..resolvers.kotlin import resolve_kotlin_import_all

                    targets = resolve_kotlin_import_all(imp.module_path, path, ctx)
                elif _lang == "scala":
                    from ..resolvers.scala import resolve_scala_import_all

                    targets = resolve_scala_import_all(imp.module_path, path, ctx)
                elif _lang in ("cpp", "c"):
                    # Fan-out across sibling TUs in the same CMake/Bazel
                    # target so a public header reached by one ``.cc`` is
                    # also marked imported by the other ``.cc`` files in
                    # the library — rescues every public-header symbol
                    # whose only declared import lives in one sibling
                    # without the workspace edge.
                    from ..resolvers.cpp import resolve_cpp_import_all

                    targets = resolve_cpp_import_all(imp.module_path, path, ctx)
                else:
                    single = resolve_import(imp.module_path, path, _lang, ctx)
                    targets = (single,) if single else ()
                if targets:
                    imp.resolved_file = targets[0]
                for target in targets:
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
            lang_import_time[_lang] = lang_import_time.get(_lang, 0.0) + (_t.monotonic() - _t0)
            if progress:
                progress.on_item_done("graph.imports")
        if progress:
            _phase_done = getattr(progress, "on_phase_done", None)
            if _phase_done is not None:
                _phase_done("graph.imports")
        if lang_import_time:
            log.info(
                "import_resolution_per_language",
                seconds={k: round(v, 2) for k, v in lang_import_time.items()},
            )

        # --- Phase 1b: Resolve non-import type references (e.g. C# ctor params) ---
        # Lives between import resolution and heritage so the type-use
        # edges feed into heritage's import_targets propagation if a
        # future language emits them, and so dead-code reachability
        # sees every edge before any analysis pass runs.
        if progress:
            progress.on_phase_start("graph.type_refs", len(self._parsed_files))
        type_use_counts = resolve_type_refs(self._parsed_files, ctx, self._graph)
        for path in self._parsed_files:
            for _, target, data in self._graph.out_edges(path, data=True):
                # Treat both real ``using`` imports and synthesised
                # type-use edges as import-like for downstream heritage
                # / call resolution. Without this, type-use edges would
                # only contribute to file-reachability — not to
                # ``import_targets`` which gates cross-file call /
                # heritage lookups.
                if data.get("edge_type") in ("imports", "type_use"):
                    import_targets.setdefault(path, set()).add(target)
        if progress:
            _phase_done = getattr(progress, "on_phase_done", None)
            if _phase_done is not None:
                _phase_done("graph.type_refs")
        del type_use_counts  # used only for logging inside resolve_type_refs

        # --- Phase 1c: Resolve C# member-access reads ---
        # Bind `var x = new T(...); ... x.Prop` style property reads
        # to T's defining file. Cuts the largest single bucket of C#
        # unused_export false positives (audit #23).
        self._resolve_member_reads(progress=progress)

        # --- Ruby rspec directory-mirror edges ---
        # Spec files carry no requires (rspec wires the helper + subject
        # at runtime); the spec/ <-> lib/ mirror convention links them.
        self._resolve_ruby_spec_mirrors(progress=progress)

        # --- C/C++ header ↔ implementation pairing ---
        # foo.h -> foo.c (same stem, same dir): consumers including the
        # header must be able to reach the implementation.
        self._resolve_cpp_header_pairs(progress=progress)

        # --- C# partial-class co-fragments ---
        # Fragments of one partial type across files are literally one
        # class; link them bidirectionally so neither reads as orphaned.
        self._resolve_csharp_partials(ctx, progress=progress)

        # --- JVM same-package implicit references ---
        # Java/Kotlin (and Scala) reference same-package types without an
        # import statement; emit conservative sibling edges so cohesive
        # packages don't read as disconnected files.
        self._resolve_jvm_same_package(ctx, progress=progress)

        # --- C# same-namespace + global-using implicit references ---
        # C# references same-namespace types with no using directive, and
        # global usings make namespaces visible project-wide; emit
        # conservative sibling edges so neither reads as orphaned.
        self._resolve_csharp_same_namespace(ctx, progress=progress)

        # --- Swift intra-module type references ---
        # Swift files see same-target siblings with no import statement;
        # emit conservative type-reference edges per SPM target.
        self._resolve_swift_same_module(ctx, progress=progress)

        # --- F# fsproj compile-order spine ---
        # fsproj <Compile Include> order is a real dependency constraint
        # (files may only reference earlier files); adjacent pairs keep
        # open-light projects connected.
        self._resolve_fsharp_compile_order(ctx, progress=progress)

        # --- Phase 2: Resolve heritage (extends/implements) ---
        self._resolve_heritage(import_targets, progress=progress)

        # --- Phase 2b: Resolve Go structural interface satisfaction ---
        # Go has no `implements` keyword; connect concrete types to the
        # interfaces their method set satisfies so interfaces reached only
        # through implementors are not flagged as dead exports.
        self._resolve_go_interface_satisfaction(progress=progress)

        # --- Phase 3: Resolve symbol-level calls ---
        self._resolve_calls(import_targets, progress=progress)

        self._built = True

        # Keep the resolver-built DotNetProjectIndex reachable after the
        # context goes out of scope: the dynamic-hints phase (XAML) needs
        # the same type map and otherwise rebuilds the index from disk.
        # Only stashed when this build pruned nested git repos, because a
        # standalone rebuild always prunes — the maps must be identical.
        self.dotnet_index = (
            getattr(ctx, "_dotnet_index", None) if self._prune_nested_git else None
        )

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

    def graph(self) -> nx.DiGraph:
        """Return the graph (building it first if necessary)."""
        if not self._built:
            self.build()
        return self._graph
