"""Composing one package's bounded relationship graph and its file expansion.

Aggregate-first: the graph never names a file. Its nodes are the first-party
communities that import the package, and a file list is a second, separately
bounded request against one of them. That is what keeps a package with
thousands of importers renderable.

Session-free for the same reason as :mod:`.summary` — one implementation
answers for a live database and for a precomputed artifact, so the caps and the
flags that confess them cannot be re-derived differently by a second consumer.

Both folds resolve the package's target nodes through the same helper, so the
evidence the graph counts is the evidence the file expansion pages. Divergence
there would let a community claim importers the drill-down could not show.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .records import Scope, field, in_scope, split_package_key, target_basis

#: Communities returned per graph, and edges, which are one per community here.
DEFAULT_RELATIONSHIP_NODE_LIMIT = 50
DEFAULT_RELATIONSHIP_EDGE_LIMIT = 200
#: Files returned per expansion page.
DEFAULT_FILE_LIMIT = 25
#: Resolved external nodes named in the response, out of those counted.
EXTERNAL_TARGET_LIMIT = 20
#: Resolved external nodes whose edges are counted at all. Beyond this the
#: response says ``evidence_truncated`` rather than quietly undercounting.
EVIDENCE_TARGET_LIMIT = 200


@dataclass(frozen=True, slots=True)
class GraphTarget:
    """One persisted external graph node linked to the selected package."""

    node_id: str
    match_basis: str

    def as_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "match_basis": self.match_basis}


@dataclass(frozen=True, slots=True)
class RelationshipNode:
    """A first-party graph community that imports the selected package."""

    aggregate_key: str
    label: str
    community_id: int
    importing_file_count: int
    import_edge_count: int
    top_file: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "aggregate_key": self.aggregate_key,
            "label": self.label,
            "community_id": self.community_id,
            "importing_file_count": self.importing_file_count,
            "import_edge_count": self.import_edge_count,
            "top_file": self.top_file,
        }


@dataclass(frozen=True, slots=True)
class RelationshipEdge:
    source: str
    target: str
    import_edge_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "import_edge_count": self.import_edge_count,
        }


@dataclass(frozen=True, slots=True)
class RelationshipGraph:
    """One package plus the bounded set of communities that reach it."""

    package_key: str
    package_name: str
    package_node_id: str
    match_basis: str
    matched_external_nodes: list[GraphTarget]
    matched_external_nodes_total: int
    matched_external_nodes_truncated: bool
    evidence_target_limit: int
    evidence_truncated: bool
    nodes: list[RelationshipNode]
    edges: list[RelationshipEdge]
    aggregate_total: int
    aggregate_returned: int
    edge_total: int
    edge_returned: int
    importing_file_total: int
    import_edge_total: int
    node_limit: int
    edge_limit: int
    truncated: bool
    scope: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_key": self.package_key,
            "package_name": self.package_name,
            "package_node_id": self.package_node_id,
            "match_basis": self.match_basis,
            "matched_external_nodes": [t.as_dict() for t in self.matched_external_nodes],
            "matched_external_nodes_total": self.matched_external_nodes_total,
            "matched_external_nodes_truncated": self.matched_external_nodes_truncated,
            "evidence_target_limit": self.evidence_target_limit,
            "evidence_truncated": self.evidence_truncated,
            "nodes": [n.as_dict() for n in self.nodes],
            "edges": [e.as_dict() for e in self.edges],
            "aggregate_total": self.aggregate_total,
            "aggregate_returned": self.aggregate_returned,
            "edge_total": self.edge_total,
            "edge_returned": self.edge_returned,
            "importing_file_total": self.importing_file_total,
            "import_edge_total": self.import_edge_total,
            "node_limit": self.node_limit,
            "edge_limit": self.edge_limit,
            "truncated": self.truncated,
            "scope": self.scope,
        }


@dataclass(frozen=True, slots=True)
class ImportingFile:
    path: str
    language: str
    import_edge_count: int
    matched_external_node_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "import_edge_count": self.import_edge_count,
            "matched_external_node_count": self.matched_external_node_count,
        }


@dataclass(frozen=True, slots=True)
class ImportingFiles:
    """One independently bounded page of files behind an aggregate node."""

    package_key: str
    aggregate_key: str
    items: list[ImportingFile]
    total: int
    returned: int
    limit: int
    offset: int
    truncated: bool
    scope: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_key": self.package_key,
            "aggregate_key": self.aggregate_key,
            "items": [item.as_dict() for item in self.items],
            "total": self.total,
            "returned": self.returned,
            "limit": self.limit,
            "offset": self.offset,
            "truncated": self.truncated,
            "scope": self.scope,
        }


def community_key(community_id: int) -> str:
    """The aggregate key that addresses one community across the surface."""
    return f"community:{community_id}"


def split_community_key(aggregate_key: str) -> int:
    """Invert :func:`community_key`, rejecting anything that is not one."""
    prefix, separator, value = aggregate_key.partition(":")
    if prefix != "community" or not separator:
        raise ValueError("aggregate_key must identify a graph community")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError("aggregate_key must identify a graph community") from exc


def _community_label(meta_json: str | None, community_id: int, top_file: str | None) -> str:
    """A community's own label, else the shared root of what it imports from."""
    try:
        label = json.loads(meta_json or "{}").get("label")
    except (json.JSONDecodeError, TypeError, AttributeError):
        label = None
    if isinstance(label, str) and label.strip():
        return label.strip()
    if top_file:
        parts = top_file.replace("\\", "/").split("/")
        return "/".join(parts[:2]) if len(parts) > 1 else parts[0]
    return f"Community {community_id}"


def resolve_targets(
    declarations: Iterable[Any],
    links: Iterable[Any],
    ecosystem: str,
    name: str,
    scope: Scope,
) -> tuple[str, list[str], int] | None:
    """The scoped, capped target universe both relationship reads work from.

    Returns ``None`` when the package is not declared in this scope, which the
    surface answers as a 404. A package declared but linked to nothing resolves
    to an empty target list, which is a different and answerable state.
    """
    declared_name: str | None = None
    for row in declarations:
        if not in_scope(field(row, "declared_in", "") or "", scope):
            continue
        if (field(row, "ecosystem", "") or "") == ecosystem and (
            field(row, "name", "") or ""
        ) == name:
            declared_name = name
            break
    if declared_name is None:
        return None
    node_ids = sorted(
        {
            node_id
            for row in links
            if (field(row, "ecosystem", "") or "") == ecosystem
            and (field(row, "name", "") or "") == name
            and (node_id := field(row, "node_id", None)) is not None
        }
    )
    return declared_name, node_ids[:EVIDENCE_TARGET_LIMIT], len(node_ids)


@dataclass(slots=True)
class _Community:
    """One community's import evidence, reduced as edges arrive."""

    community_id: int
    import_edge_count: int = 0
    importing_files: set[str] = dataclasses.field(default_factory=set)
    top_file: str | None = None
    community_meta_json: str | None = None


def _group_by_community(
    import_edges: Iterable[Any], targets: set[str], scope: Scope
) -> list[_Community]:
    """Fold the edges reaching ``targets`` into ranked per-community aggregates."""
    groups: dict[int, _Community] = {}
    for row in import_edges:
        source = field(row, "source_path", None)
        if field(row, "target_node_id", None) not in targets:
            continue
        if not in_scope(source, scope):
            continue
        community_id = int(field(row, "community_id", 0) or 0)
        group = groups.get(community_id)
        if group is None:
            group = _Community(community_id=community_id)
            groups[community_id] = group
        group.import_edge_count += 1
        group.importing_files.add(source)
        if group.top_file is None or source < group.top_file:
            group.top_file = source
        meta = field(row, "community_meta_json", None)
        if meta is not None and (
            group.community_meta_json is None or meta > group.community_meta_json
        ):
            group.community_meta_json = meta
    ranked = sorted(
        groups.values(),
        key=lambda g: (-len(g.importing_files), -g.import_edge_count, g.community_id),
    )
    return ranked


def build_relationship_graph(
    declarations: Iterable[Any],
    links: Iterable[Any],
    import_edges: Iterable[Any],
    package_key: str,
    *,
    scope: Scope = "primary",
    node_limit: int = DEFAULT_RELATIONSHIP_NODE_LIMIT,
    edge_limit: int = DEFAULT_RELATIONSHIP_EDGE_LIMIT,
) -> RelationshipGraph | None:
    """Fold one package's importers into bounded per-community aggregates.

    ``import_edges`` are the file-sourced import edges, each carrying
    ``source_path``, ``target_node_id``, ``community_id`` and
    ``community_meta_json`` for the importing file's community. A
    ``package_key`` that names no ecosystem and package raises ``ValueError``.
    """
    ecosystem, name = split_package_key(package_key)
    resolved = resolve_targets(declarations, links, ecosystem, name, scope)
    if resolved is None:
        return None
    package_name, target_nodes, target_total = resolved
    matched = [
        GraphTarget(node_id=node_id, match_basis=target_basis(node_id, package_name))
        for node_id in target_nodes[:EXTERNAL_TARGET_LIMIT]
    ]
    bases = {target_basis(node_id, package_name) for node_id in target_nodes}
    match_basis = "unresolved" if not bases else next(iter(bases)) if len(bases) == 1 else "mixed"
    evidence_truncated = target_total > len(target_nodes)
    package_node_id = f"package:{package_key}"

    ranked = _group_by_community(import_edges, set(target_nodes), scope) if target_nodes else []
    returned = ranked[: min(node_limit, edge_limit)]
    nodes = [
        RelationshipNode(
            aggregate_key=community_key(group.community_id),
            label=_community_label(group.community_meta_json, group.community_id, group.top_file),
            community_id=group.community_id,
            importing_file_count=len(group.importing_files),
            import_edge_count=group.import_edge_count,
            top_file=group.top_file,
        )
        for group in returned
    ]
    edges = [
        RelationshipEdge(
            source=node.aggregate_key,
            target=package_node_id,
            import_edge_count=node.import_edge_count,
        )
        for node in nodes
    ]
    return RelationshipGraph(
        package_key=package_key,
        package_name=package_name,
        package_node_id=package_node_id,
        match_basis=match_basis,
        matched_external_nodes=matched,
        matched_external_nodes_total=target_total,
        matched_external_nodes_truncated=target_total > len(matched),
        evidence_target_limit=EVIDENCE_TARGET_LIMIT,
        evidence_truncated=evidence_truncated,
        nodes=nodes,
        edges=edges,
        aggregate_total=len(ranked),
        aggregate_returned=len(nodes),
        edge_total=len(ranked),
        edge_returned=len(edges),
        importing_file_total=sum(len(g.importing_files) for g in ranked),
        import_edge_total=sum(g.import_edge_count for g in ranked),
        node_limit=node_limit,
        edge_limit=edge_limit,
        truncated=len(ranked) > len(nodes) or evidence_truncated,
        scope=scope,
    )


def build_importing_files(
    declarations: Iterable[Any],
    links: Iterable[Any],
    import_edges: Iterable[Any],
    package_key: str,
    aggregate_key: str,
    *,
    scope: Scope = "primary",
    limit: int = DEFAULT_FILE_LIMIT,
    offset: int = 0,
) -> ImportingFiles | None:
    """Page the files behind one community aggregate, bounded independently.

    Raises ``ValueError`` if either key is not the shape its builder emits.
    """
    ecosystem, name = split_package_key(package_key)
    community_id = split_community_key(aggregate_key)
    resolved = resolve_targets(declarations, links, ecosystem, name, scope)
    if resolved is None:
        return None
    _, target_nodes, _ = resolved

    per_file: dict[str, tuple[str, int, set[str]]] = {}
    if target_nodes:
        targets = set(target_nodes)
        for row in import_edges:
            source = field(row, "source_path", None)
            target = field(row, "target_node_id", None)
            if target not in targets:
                continue
            if int(field(row, "community_id", 0) or 0) != community_id:
                continue
            if not in_scope(source, scope):
                continue
            language, count, matched_nodes = per_file.get(source, ("", 0, set()))
            per_file[source] = (
                language or (field(row, "language", "") or ""),
                count + 1,
                matched_nodes | {target},
            )

    ranked = sorted(per_file.items(), key=lambda entry: (-entry[1][1], entry[0]))
    page = ranked[offset : offset + limit]
    items = [
        ImportingFile(
            path=path,
            language=language,
            import_edge_count=count,
            matched_external_node_count=len(matched_nodes),
        )
        for path, (language, count, matched_nodes) in page
    ]
    return ImportingFiles(
        package_key=package_key,
        aggregate_key=aggregate_key,
        items=items,
        total=len(ranked),
        returned=len(items),
        limit=limit,
        offset=offset,
        truncated=offset + len(items) < len(ranked),
        scope=scope,
    )


__all__ = [
    "DEFAULT_FILE_LIMIT",
    "DEFAULT_RELATIONSHIP_EDGE_LIMIT",
    "DEFAULT_RELATIONSHIP_NODE_LIMIT",
    "EVIDENCE_TARGET_LIMIT",
    "EXTERNAL_TARGET_LIMIT",
    "GraphTarget",
    "ImportingFile",
    "ImportingFiles",
    "RelationshipEdge",
    "RelationshipGraph",
    "RelationshipNode",
    "build_importing_files",
    "build_relationship_graph",
    "community_key",
    "resolve_targets",
    "split_community_key",
]
