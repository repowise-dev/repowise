"""Co-change, dynamic-hint, and framework-aware edge construction.

Mixed into :class:`GraphBuilder`. These passes add edges that static import
resolution cannot infer: git co-change relations, dynamic-dispatch hints, and
framework-mediated wiring.
"""

from __future__ import annotations

import json

import structlog

from ..resolvers import ResolverContext
from ..resolvers.go import read_go_module_path, read_go_modules
from ._stem import build_stem_map

log = structlog.get_logger(__name__)


class EdgesMixin:
    """Non-import edge construction for :class:`GraphBuilder`."""

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
        if count:
            self._invalidate_subgraph_caches()
        return count

    def update_co_change_edges(self, updated_meta: dict, min_count: int = 3) -> None:
        """Remove old co_changes edges for updated files, add new ones."""
        edges_to_remove = []
        for u, v, data in self._graph.edges(data=True):
            if data.get("edge_type") == "co_changes" and (u in updated_meta or v in updated_meta):
                edges_to_remove.append((u, v))
        self._graph.remove_edges_from(edges_to_remove)
        self.add_co_change_edges(updated_meta, min_count)
        self._invalidate_subgraph_caches()

    def add_dynamic_edges(self, edges: list) -> None:
        """Add dynamic-hint edges to the graph. Each edge is a DynamicEdge."""
        for e in edges:
            if e.source not in self._graph:
                continue
            if e.target not in self._graph:
                if self._exclude.patterns and self._exclude.match_file(e.target):
                    continue
                self._graph.add_node(e.target)
            sub_type = e.edge_type or "dynamic"
            graph_edge_type = (
                sub_type if sub_type.startswith("dynamic") else f"dynamic_{sub_type}"
            )
            self._graph.add_edge(
                e.source,
                e.target,
                edge_type=graph_edge_type,
                hint_source=e.hint_source,
                weight=e.weight,
            )
            # Propagate test marker: if hint_source ends with ":test", tag the
            # source file node as is_test so downstream consumers can filter it.
            if e.hint_source and e.hint_source.endswith(":test"):
                self._graph.nodes[e.source]["is_test"] = True
        if edges:
            self._invalidate_subgraph_caches()

    def add_framework_edges(self, tech_stack: list[str] | None = None) -> int:
        """Add synthetic edges for framework-mediated relationships.

        Returns the number of edges added.
        """
        from ..framework_edges import add_framework_edges

        path_set = set(self._parsed_files.keys())
        stem_map = build_stem_map(path_set)

        go_modules = read_go_modules(self._repo_path)
        ctx = ResolverContext(
            path_set=path_set,
            stem_map=stem_map,
            graph=self._graph,
            repo_path=self._repo_path,
            tsconfig_resolver=self._tsconfig_resolver,
            go_module_path=(go_modules[-1][1] if go_modules else read_go_module_path(self._repo_path)),
            go_modules=go_modules,
            has_sfc_files=any(p.endswith((".vue", ".svelte", ".astro")) for p in path_set),
            parsed_files=self._parsed_files,
        )

        count = add_framework_edges(self._graph, self._parsed_files, ctx, tech_stack)
        if count:
            log.info("Framework edges added", count=count)
            self._invalidate_subgraph_caches()
        return count
