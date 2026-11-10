"""Co-change, document, dynamic-hint, and framework-aware edge construction.

Mixed into :class:`GraphBuilder`. These passes add edges that static import
resolution cannot infer: git co-change relations, dynamic-dispatch hints, and
framework-mediated wiring.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from ...co_change import (
    MIN_CO_CHANGE_SUPPORT,
    STRUCTURAL_CORROBORATED,
    STRUCTURAL_NOT_APPLICABLE,
    STRUCTURAL_UNEXPLAINED,
    canonical_pair,
    parse_partners,
)
from ..models import DOCUMENTATION_EDGE_TYPES, FILE_DEPENDENCY_EDGE_TYPES, is_dynamic_edge
from ..resolvers import ResolverContext
from ..resolvers.go import read_go_module_path, read_go_modules
from ..resolvers.typescript import detect_platform_files
from ._stem import build_stem_map

if TYPE_CHECKING:
    from ...analysis.doc_drift.models import DocReference, DriftKind
    from ...analysis.doc_drift.resolver import RepoIndex

log = structlog.get_logger(__name__)


class _DocumentEdgesHost(Protocol):
    _graph: Any
    _parsed_files: dict[str, Any]
    _source_map: dict[str, bytes]
    _repo_path: Path | None


def _merge_dynamic_hint(existing: dict, hint) -> None:
    """Fold a dynamic hint into the file edge already joining the same two files.

    The graph holds one edge per pair, and hints run after static resolution,
    so replacing the edge would drop what the import said (its type, the names
    it binds). Names are unioned instead. A whole-module hint (no names) on a
    static edge is kept as ``dynamic_hint`` so dead code still treats every
    public member as reached. ``dynamic_hint`` lives on the live graph only:
    persisted rows keep the static type and the unioned names (upgrade path: a
    column, if a rehydrated graph ever runs the export rescue).
    """
    if is_dynamic_edge(existing.get("edge_type")):
        # Two hints on one pair: a whole-module reach absorbs a named one.
        if not hint.imported_names or not existing.get("imported_names"):
            existing.pop("imported_names", None)
            return
    elif not hint.imported_names:
        existing["dynamic_hint"] = hint.hint_source
        return
    names = existing.get("imported_names") or []
    existing["imported_names"] = names + [n for n in hint.imported_names if n not in names]


def _document_text(host: _DocumentEdgesHost, doc: str) -> str | None:
    """A document's text, or None when there is none to read."""
    from ..document_extractors import DocumentExtractionError
    from ..source_reader import read_source
    from ..source_text import decode_source

    # The map holds converted Markdown; disk holds the original (a PDF).
    data = host._source_map.get(doc)
    if data is None and host._repo_path is not None:
        try:
            data = read_source(host._repo_path, doc)
        except DocumentExtractionError:
            return None  # the parse already reported it; no text, no edge
    return None if data is None else decode_source(data, errors="replace")


def _document_targets(
    host: _DocumentEdgesHost, doc: str, text: str, index: RepoIndex
) -> dict[str, DriftKind]:
    """The parsed files ``doc`` names, each with its strongest reference kind."""
    from ...analysis.doc_drift.extractor import extract
    from ...analysis.doc_drift.models import DriftKind

    strongest: dict[str, DriftKind] = {}
    for ref in extract(text, doc):
        target = _reference_target(index, ref, doc)
        if target is None or target == doc:
            continue
        if target in host._parsed_files and strongest.get(target) is not DriftKind.LINK:
            strongest[target] = ref.kind
    return strongest


def _reference_target(index: RepoIndex, ref: DocReference, doc: str) -> str | None:
    """The file one link or path reference resolves to, else None."""
    from ...analysis.doc_drift.models import DriftKind, DriftVerdict
    from ...analysis.doc_drift.resolver import join_relative, resolve

    if ref.kind not in (DriftKind.LINK, DriftKind.PATH):
        return None
    res = resolve(index, ref)
    # Same filter as ``_Tally.resolve_document``, narrowed to files.
    if res.verdict is not DriftVerdict.RESOLVED:
        return None
    if ref.kind is DriftKind.PATH:
        target = res.resolved_target.rstrip("/")
        # A bare name hits the root file first; `package.json` is generic prose, not that file.
        return None if "/" not in ref.raw and "/" not in target else target
    # Relative to its document; a leading "/" is the repository root.
    return ref.target if ref.raw.startswith("/") else join_relative(doc, ref.target)


def _place_document_edge(graph: Any, doc: str, target: str, kind: DriftKind) -> None:
    """Add the ``documents`` edge ``doc`` -> ``target`` unless another edge holds the pair."""
    from ...analysis.doc_drift.models import DriftKind

    existing = graph.get_edge_data(doc, target)
    if existing is not None and existing.get("edge_type") != "co_changes":
        return  # never overwrite another edge on the pair; a DiGraph holds one
    # co_changes only joins files nothing else links (add_co_change_edges).
    for u, v in ((doc, target), (target, doc)):
        if graph.get_edge_data(u, v, {}).get("edge_type") == "co_changes":
            graph.remove_edge(u, v)
    link = kind is DriftKind.LINK
    graph.add_edge(
        doc,
        target,
        edge_type="documents",
        hint_source="doc_link" if link else "doc_path",
        confidence=1.0 if link else 0.8,
    )


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
        depends on how wide the commits were. Both ends must already be graph
        nodes and each edge runs in canonical order, so the result depends only
        on the pairs, not on which files ``git_meta_map`` keys or its order:
        update passes every tracked file's partners, init only indexed files.
        """
        count = 0
        seen: set[tuple[str, str]] = set()

        for file_path, meta in git_meta_map.items():
            if file_path not in self._graph:
                continue
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

                if not self._graph.has_edge(*pair) and not self._graph.has_edge(pair[1], pair[0]):
                    self._graph.add_edge(
                        *pair,
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

    def _resolve_document_edges(self: _DocumentEdgesHost) -> None:
        """Link each Markdown document to the repository files it names.

        Global on every build, scoped ones too: an unchanged document gains its
        edge once the file it names appears. Only a link or path doc drift
        resolves to exactly one parsed file counts -- a missing edge is fine, a
        wrong one is not.
        """
        from ...analysis.doc_drift.extractor import is_own_prose
        from ...analysis.doc_drift.resolver import RepoIndex

        graph = self._graph
        graph.remove_edges_from(
            [
                (u, v)
                for u, v, d in graph.edges(data=True)
                if d.get("edge_type") in DOCUMENTATION_EDGE_TYPES
            ]
        )
        index = None
        for doc, parsed in self._parsed_files.items():
            if parsed.file_info.language != "markdown" or not is_own_prose(doc):
                continue
            text = _document_text(self, doc)
            if text is None:
                continue
            if index is None:
                index = RepoIndex.build(frozenset(self._parsed_files), {})
            for target, kind in _document_targets(self, doc, text, index).items():
                _place_document_edge(graph, doc, target, kind)

    def add_dynamic_edges(self, edges: list) -> None:
        """Add dynamic-hint edges to the graph. Each edge is a DynamicEdge."""
        for e in edges:
            # A hint naming its own file (a package.json exporting
            # "./package.json") is not a dependency.
            if e.source not in self._graph or e.source == e.target:
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
            existing = self._graph.get_edge_data(e.source, e.target)
            if existing is not None and existing.get("edge_type") in FILE_DEPENDENCY_EDGE_TYPES:
                _merge_dynamic_hint(existing, e)
                continue
            names = {"imported_names": list(e.imported_names)} if e.imported_names else {}
            self._graph.add_edge(
                e.source,
                e.target,
                edge_type=graph_edge_type,
                hint_source=e.hint_source,
                weight=e.weight,
                **names,
            )
            # A ``:test`` hint used to set ``is_test`` on the source node. Only
            # the Rust hinter emits one, for `#[test]` / `#[cfg(test)]` markers,
            # and Rust keeps its unit tests *inside* the production file - so
            # every `src/lib.rs` with an inline `mod tests` was marked a test
            # file wholesale and dropped from dead-code analysis, the knowledge
            # graph and key-concept selection (#1103). The marker means "contains
            # tests", not "is a test", and as a file → same-file hint it is now
            # dropped by the self-loop guard above. Health computes the
            # file-level version itself, from the source, as
            # ``FileContext.has_inline_tests``.
        if edges:
            self._invalidate_subgraph_caches()

    def add_framework_edges(self, tech_stack: list[str] | None = None) -> int:
        """Add synthetic edges for framework-mediated relationships.

        Returns the number of edges added.
        """
        from ..framework_edges import add_framework_edges
        from ..framework_edges.scan_cache import ScanCache

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
            has_platform_files=detect_platform_files(path_set),
            parsed_files=self._parsed_files,
            # Same reason ``build()`` passes it: a handler that scans file text
            # (the Godot ``class_name`` one covers every ``.gd`` in the repo)
            # reads the bytes ingestion already has instead of a second pass
            # over the tree. Empty unless ``set_source_map`` was called.
            source_map=self._source_map,
            pytest_roots=self._pytest_roots,
            framework_scans=ScanCache(getattr(self, "_cache_dir", None)),
        )

        # The .NET project index ``build()`` already made; handlers that scope
        # by project read it instead of walking the tree for every .csproj again.
        if getattr(self, "dotnet_index", None) is not None:
            ctx._dotnet_index = self.dotnet_index
        count = add_framework_edges(self._graph, self._parsed_files, ctx, tech_stack)
        ctx.framework_scans.save()
        if count:
            log.info("Framework edges added", count=count)
            self._invalidate_subgraph_caches()
        return count
