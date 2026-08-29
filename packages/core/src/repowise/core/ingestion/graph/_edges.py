"""Co-change, dynamic-hint, and framework-aware edge construction.

Mixed into :class:`GraphBuilder`. These passes add edges that static import
resolution cannot infer: git co-change relations, dynamic-dispatch hints, and
framework-mediated wiring.
"""

from __future__ import annotations

import structlog

from ...co_change import (
    MIN_CO_CHANGE_SUPPORT,
    STRUCTURAL_CORROBORATED,
    STRUCTURAL_NOT_APPLICABLE,
    STRUCTURAL_UNEXPLAINED,
    canonical_pair,
    parse_partners,
)
from ..resolvers import ResolverContext
from ..resolvers.go import read_go_module_path, read_go_modules
from ._stem import build_stem_map

log = structlog.get_logger(__name__)


class EdgesMixin:
    """Non-import edge construction for :class:`GraphBuilder`."""

    def label_co_change_structure(self, partners_by_file: dict[str, list[dict]]) -> int:
        """Record, on each partner, whether the graph explains the pair.

        Co-change is mined from git with no view of the code, so this is the
        only point where both are in hand. Writes ``structural`` and, when a
        dependency was found, ``dependency_kind`` in place; returns how many
        pairs came back unexplained.

        An edge that exists is checked first, so a pair is never dismissed as
        not-applicable while the graph is holding the very edge that explains
        it. Only when nothing was found does it matter whether anything
        *could* have been -- see
        :meth:`~...analysis.graph_view.ImportEdgeView.can_carry_dependency`.

        Takes decoded partner lists; the wrapper in the git phase handles the
        JSON column the records are persisted in.
        """
        # Deferred: every other ingestion import of analysis is, to keep the
        # package pair acyclic at module load.
        from ...analysis.graph_view import ImportEdgeView

        if not self._graph.number_of_nodes():
            # Nothing to answer with, and writing "not applicable" for every
            # pair would erase a label an earlier run got right.
            return 0
        view = ImportEdgeView(self._graph)
        unexplained = 0
        for file_path, partners in partners_by_file.items():
            eligible_self = view.can_carry_dependency(file_path)
            for record in partners:
                if not isinstance(record, dict):
                    continue
                partner_path = record.get("file_path") or record.get("path")
                if not partner_path:
                    continue
                partner_path = str(partner_path)
                kind = view.dependency_kind(file_path, partner_path)
                if kind is not None:
                    record["structural"] = STRUCTURAL_CORROBORATED
                    record["dependency_kind"] = kind
                    continue
                record.pop("dependency_kind", None)
                if not eligible_self or not view.can_carry_dependency(partner_path):
                    record["structural"] = STRUCTURAL_NOT_APPLICABLE
                else:
                    record["structural"] = STRUCTURAL_UNEXPLAINED
                    unexplained += 1
        log.info("Co-change structure labelled", unexplained=unexplained)
        return unexplained

    def add_co_change_edges(
        self, git_meta_map: dict, min_support: int = MIN_CO_CHANGE_SUPPORT
    ) -> int:
        """Add co_changes edges from git metadata. Returns count of edges added.

        Gated on shared commits rather than the decayed weight, whose scale
        depends on how wide the commits were.
        """
        count = 0
        seen: set[tuple[str, str]] = set()

        for file_path, meta in git_meta_map.items():
            for partner in parse_partners(meta.get("co_change_partners_json")):
                partner_path, co_count = partner.file_path, partner.weight
                if partner.support < min_support:
                    continue
                if partner_path not in self._graph:
                    continue

                pair = canonical_pair(file_path, partner_path)
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

    def update_co_change_edges(
        self, updated_meta: dict, min_support: int = MIN_CO_CHANGE_SUPPORT
    ) -> None:
        """Remove old co_changes edges for updated files, add new ones."""
        edges_to_remove = []
        for u, v, data in self._graph.edges(data=True):
            if data.get("edge_type") == "co_changes" and (u in updated_meta or v in updated_meta):
                edges_to_remove.append((u, v))
        self._graph.remove_edges_from(edges_to_remove)
        self.add_co_change_edges(updated_meta, min_support)
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
            # `DynamicEdge.edge_type` is a `DynamicKind`, so there is no empty
            # case to fall back on. The `or "dynamic"` that used to sit here
            # was the only writer of a bare `"dynamic"` edge — unreachable in
            # practice (0 rows in 42 indexes) but enough to keep `"dynamic"`
            # in the declared vocabulary, which in turn is what three
            # consumers wrote their sets against and why none of them matched
            # a real `dynamic_*` edge.
            sub_type = e.edge_type
            graph_edge_type = sub_type if sub_type.startswith("dynamic") else f"dynamic_{sub_type}"
            self._graph.add_edge(
                e.source,
                e.target,
                edge_type=graph_edge_type,
                hint_source=e.hint_source,
                weight=e.weight,
            )
            # A ``:test`` hint used to set ``is_test`` on the source node. Only
            # the Rust hinter emits one, for `#[test]` / `#[cfg(test)]` markers,
            # and Rust keeps its unit tests *inside* the production file - so
            # every `src/lib.rs` with an inline `mod tests` was marked a test
            # file wholesale and dropped from dead-code analysis, the knowledge
            # graph and key-concept selection (#1103). The marker means "contains
            # tests", not "is a test": it stays recorded on this edge's
            # ``hint_source``, where it says that and nothing more. Health
            # computes the file-level version itself, from the source, as
            # ``FileContext.has_inline_tests``.
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
