"""(a) Context assembly for domain synthesis.

Pure functions that gather the bounded context the LLM prompts need, drawn from
the already-generated structural artifacts (enriched layers, file node summaries,
the heaviest import edges) - never from raw source. No DB, no LLM, no I/O, so
every function here is unit-testable against small fixture dicts.

Two contexts are assembled:

* a per-layer **cluster** summary feeding the domain-naming prompt, and
* a per-domain **member** view (ranked files + summaries + internal import
  edges) feeding the per-domain flow/step prompt.

Cross-domain dependency edges are derived structurally from the import edges
that cross a domain boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import CrossDomainEdge, DomainNode


@dataclass
class LayerCluster:
    """Compact view of one structural layer for the naming prompt."""

    layer_id: str
    name: str
    description: str
    file_count: int
    top_files: list[str] = field(default_factory=list)


@dataclass
class FileContext:
    """A member file plus its best available one-line summary."""

    node_id: str  # "file:<path>"
    path: str
    summary: str = ""


def _is_file_node(node: dict) -> bool:
    nid = node.get("id")
    return isinstance(nid, str) and nid.startswith("file:")


def _node_path(node: dict) -> str:
    return node.get("filePath") or str(node.get("id", "")).removeprefix("file:")


def _best_summary(node: dict, page_summaries: dict[str, str]) -> str:
    """Prefer a rich wiki-page summary; fall back to the node's own summary."""
    path = _node_path(node)
    return page_summaries.get(path) or str(node.get("summary") or "")


def file_nodes_by_id(nodes: list[dict]) -> dict[str, dict]:
    return {n["id"]: n for n in nodes if _is_file_node(n) and n.get("id")}


def _rank(node: dict) -> float:
    try:
        return float(node.get("pagerank") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_layer_clusters(
    layers: list[dict],
    nodes: list[dict],
    max_files: int = 12,
) -> list[LayerCluster]:
    """One :class:`LayerCluster` per structural layer, ranked top files first.

    Empty layers (no file members) are dropped - a domain can only be named
    from a real cluster.
    """
    by_id = file_nodes_by_id(nodes)
    clusters: list[LayerCluster] = []
    for layer in layers:
        member_ids = [nid for nid in layer.get("nodeIds", []) if nid in by_id]
        if not member_ids:
            continue
        ranked = sorted(member_ids, key=lambda nid: _rank(by_id[nid]), reverse=True)
        top = [_node_path(by_id[nid]) for nid in ranked[:max_files]]
        clusters.append(
            LayerCluster(
                layer_id=str(layer.get("id", "")),
                name=str(layer.get("name", "")),
                description=str(layer.get("description", "")),
                file_count=len(member_ids),
                top_files=top,
            )
        )
    return clusters


def member_node_ids(layer_ids: list[str], layers: list[dict], nodes: list[dict]) -> list[str]:
    """Union of the file node ids belonging to *layer_ids* (deduped, sorted)."""
    by_id = file_nodes_by_id(nodes)
    wanted = set(layer_ids)
    out: set[str] = set()
    for layer in layers:
        if str(layer.get("id", "")) in wanted:
            out.update(nid for nid in layer.get("nodeIds", []) if nid in by_id)
    return sorted(out)


def build_member_context(
    domain: DomainNode,
    nodes: list[dict],
    page_summaries: dict[str, str],
    max_files: int = 25,
) -> list[FileContext]:
    """Ranked member files with their best summaries, for the flow prompt."""
    by_id = file_nodes_by_id(nodes)
    members = [nid for nid in domain.member_node_ids if nid in by_id]
    ranked = sorted(members, key=lambda nid: _rank(by_id[nid]), reverse=True)
    out: list[FileContext] = []
    for nid in ranked[:max_files]:
        node = by_id[nid]
        out.append(
            FileContext(
                node_id=nid,
                path=_node_path(node),
                summary=_best_summary(node, page_summaries),
            )
        )
    return out


def heaviest_internal_edges(
    member_node_ids_set: set[str],
    edges: list[dict],
    top: int = 15,
) -> list[tuple[str, str]]:
    """The highest-weight import edges with both endpoints inside the domain.

    Returns ``(source_path, target_path)`` pairs so the prompt can show real
    "A is used by B" coupling without leaking node-id noise.
    """
    scored: list[tuple[float, str, str]] = []
    for edge in edges:
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        if src in member_node_ids_set and tgt in member_node_ids_set:
            weight = float(edge.get("weight", 1) or 1)
            scored.append((weight, src, tgt))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [
        (src.removeprefix("file:"), tgt.removeprefix("file:")) for _, src, tgt in scored[:top]
    ]


def internal_edge_ids(
    member_node_ids_set: set[str],
    edges: list[dict],
) -> list[tuple[str, str]]:
    """All ``(source, target)`` node-id pairs with both ends inside the member
    set, sorted. Feeds the domain fingerprint so a coupling change among
    otherwise-unchanged members still forces a re-synthesis."""
    pairs = {
        (edge.get("source", ""), edge.get("target", ""))
        for edge in edges
        if edge.get("source", "") in member_node_ids_set
        and edge.get("target", "") in member_node_ids_set
    }
    return sorted(pairs)


def derive_cross_domain_edges(
    domains: list[DomainNode],
    edges: list[dict],
) -> list[CrossDomainEdge]:
    """Aggregate import edges that cross a domain boundary into domain->domain
    dependency edges weighted by the count of crossing imports."""
    domain_of: dict[str, str] = {}
    for domain in domains:
        for nid in domain.member_node_ids:
            domain_of[nid] = domain.id

    counts: dict[tuple[str, str], int] = {}
    for edge in edges:
        src_domain = domain_of.get(edge.get("source", ""))
        tgt_domain = domain_of.get(edge.get("target", ""))
        if src_domain and tgt_domain and src_domain != tgt_domain:
            key = (src_domain, tgt_domain)
            counts[key] = counts.get(key, 0) + 1

    return [
        CrossDomainEdge(source=src, target=tgt, weight=weight)
        for (src, tgt), weight in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
