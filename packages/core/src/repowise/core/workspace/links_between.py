"""Every link between two services, in both directions, one page at a time.

The system graph folds a pair's evidence into one edge per ``(source, target,
kind)`` and keeps at most :data:`.system_graph.MAX_EDGE_REFS` refs on it. This
is the read behind such an edge: the contract links, service links, package
dependencies and co-changes themselves, each end resolved to its service with
the resolver the graph builder used, so a row lands on exactly the edge the
map drew and a pair's rows add up to its edges' weights.

Pure over the stored artifacts: :func:`build_links_index` reads the parsed
``system_graph.json``, ``contracts.json`` and ``cross_repo_edges.json``
payloads once, and :func:`links_between` pages one pair out of that index. Any
server holding those payloads builds the same index and answers the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from repowise.core.workspace.reads import _consumer_id, _link_haystack, _matches_terms, link_row
from repowise.core.workspace.system_graph import (
    boundaries_from_nodes,
    edge_kind_for_contract_type,
    resolve_service_id,
)

__all__ = ["A_TO_B", "BOTH", "B_TO_A", "LinksIndex", "build_links_index", "links_between"]

#: The row's ``direction``: ``a`` calls / depends on ``b``, the reverse, or
#: both ways at once (a co-change, which has no direction).
A_TO_B = "a_to_b"
B_TO_A = "b_to_a"
BOTH = "both"

_PACKAGE_DEP_FIELDS = (
    "source_repo",
    "source_manifest",
    "target_repo",
    "target_package",
    "kind",
    "target_manifest",
    "requested_version",
    "scope",
    "resolution_basis",
)
_CO_CHANGE_FIELDS = (
    "source_repo",
    "source_file",
    "target_repo",
    "target_file",
    "strength",
    "frequency",
    "last_date",
)


@dataclass(frozen=True)
class _Entry:
    source: str
    target: str
    kind: str
    evidence: str  # contract | package | co_change
    raw: dict
    haystack: str
    order: tuple[str, ...]


@dataclass
class LinksIndex:
    """Every stored link of the estate, resolved once to the service pair it joins.

    Build with :func:`build_links_index`; a server keeps one per loaded set of
    artifacts and pages every pair out of it.
    """

    nodes: dict[str, dict] = field(default_factory=dict)
    #: Unordered pair ``(min id, max id)`` -> its entries, both directions.
    pairs: dict[tuple[str, str], list[_Entry]] = field(default_factory=dict)
    #: ``(role, repo, file, contract_id) -> origin``; first contract row wins.
    origins: dict[tuple, str | None] = field(default_factory=dict)
    #: The graph and the contracts come from different builds, so some links
    #: may resolve to services the graph no longer has and drop out.
    stale_graph: bool = False

    def _add(self, entry: _Entry) -> None:
        if entry.source == entry.target:
            return  # calls inside one service are never an edge
        key = (min(entry.source, entry.target), max(entry.source, entry.target))
        self.pairs.setdefault(key, []).append(entry)


def build_links_index(graph: dict, store: dict, overlay: dict | None = None) -> LinksIndex:
    """Resolve every link, package dependency and co-change of the estate to its pair.

    *graph* is ``system_graph.json`` (its ``nodes`` and ``repo_provenance``),
    *store* is ``contracts.json`` (``contracts``, ``contract_links``,
    ``service_links``, ``repo_provenance``) and *overlay* is
    ``cross_repo_edges.json`` (``package_deps``, ``co_changes``). Each end is
    resolved the way :func:`.system_graph.build_system_graph` resolved it.

    The premise is that the three come from one workspace update, which writes
    them together. When the graph and the contracts both record the repo heads
    they were built from and those differ, ``stale_graph`` says so.
    """
    nodes = graph.get("nodes", [])
    graph_prov, store_prov = graph.get("repo_provenance"), store.get("repo_provenance")
    index = LinksIndex(
        nodes={n["id"]: n for n in nodes},
        stale_graph=bool(graph_prov and store_prov and graph_prov != store_prov),
    )
    boundaries = boundaries_from_nodes(nodes)

    def resolve(repo: str, file_path: str | None) -> str:
        return resolve_service_id(repo, file_path, boundaries)

    for lk in _rows(
        [*store.get("contract_links", []), *store.get("service_links", [])],
        "consumer_repo",
        "provider_repo",
    ):
        # A service link has no provider file, so it lands on the repo-root node.
        index._add(
            _Entry(
                source=resolve(lk["consumer_repo"], lk.get("consumer_file")),
                target=resolve(lk["provider_repo"], lk.get("provider_file") or None),
                kind=edge_kind_for_contract_type(lk.get("contract_type", "")),
                evidence="contract",
                raw=lk,
                haystack=_link_haystack(lk),
                order=_order(lk, "contract_id", "consumer_file", "provider_file"),
            )
        )
    for dep in _rows((overlay or {}).get("package_deps", []), "source_repo", "target_repo"):
        index._add(
            _Entry(
                source=resolve(dep["source_repo"], dep.get("source_manifest")),
                target=resolve(dep["target_repo"], dep.get("target_manifest") or None),
                kind="package",
                evidence="package",
                raw=dep,
                haystack=_haystack(dep, _PACKAGE_DEP_FIELDS),
                order=_order(dep, "target_package", "source_manifest"),
            )
        )
    for cc in _rows((overlay or {}).get("co_changes", []), "source_repo", "target_repo"):
        index._add(
            _Entry(
                source=resolve(cc["source_repo"], cc.get("source_file")),
                target=resolve(cc["target_repo"], cc.get("target_file")),
                kind="co_change",
                evidence="co_change",
                raw=cc,
                haystack=_haystack(cc, _CO_CHANGE_FIELDS),
                order=_order(cc, "source_file", "target_file"),
            )
        )
    for c in store.get("contracts", []):
        key = (c.get("role"), c.get("repo"), c.get("file_path"), c.get("contract_id"))
        index.origins.setdefault(key, c.get("origin"))
    return index


def links_between(
    index: LinksIndex,
    *,
    a: str,
    b: str,
    kind: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any] | None:
    """The links between service nodes *a* and *b*, both directions, paged.

    Every row carries its edge ``kind``, its ``direction`` (:data:`A_TO_B` when
    *a* is the consumer or dependent, :data:`BOTH` for a co-change) and its
    ``evidence``: a ``contract`` row holds the contract-link row
    (:func:`.reads.link_row`) and both ends' contract origins, a ``package``
    row the manifest dependency, a ``co_change`` row the file pair.

    ``q`` keeps rows where every whitespace-separated term matches, ``kind``
    keeps one edge kind. ``total`` counts the filtered rows the page is cut
    from; ``totals`` counts every kind and direction under ``q`` but not
    ``kind``, so a caller grouping kinds into families sums them itself.

    None when *a* or *b* is not a node of the graph.
    """
    if a not in index.nodes or b not in index.nodes:
        return None
    terms = (q or "").lower().split()
    matched = [
        (_direction(e, a), e)
        for e in index.pairs.get((min(a, b), max(a, b)), [])
        if not terms or _matches_terms(e.haystack, terms)
    ]

    totals: dict[tuple[str, str], int] = {}
    for direction, e in matched:
        totals[(e.kind, direction)] = totals.get((e.kind, direction), 0) + 1

    rows = sorted(
        (m for m in matched if kind is None or m[1].kind == kind),
        key=lambda m: (m[0], m[1].kind, m[1].evidence, *m[1].order),
    )
    return {
        "a": a,
        "b": b,
        "stale_graph": index.stale_graph,
        "links": [_row(e, d, index.origins) for d, e in rows[offset : offset + limit]],
        "total": len(rows),
        "totals": [{"kind": k, "direction": d, "count": n} for (k, d), n in sorted(totals.items())],
    }


def _rows(rows: list[dict], *required: str) -> list[dict]:
    """*rows* that name both repos; a malformed stored row is skipped, as the builder never saw it."""
    return [r for r in rows if all(r.get(k) for k in required)]


def _direction(entry: _Entry, a: str) -> str:
    if entry.evidence == "co_change":
        return BOTH
    return A_TO_B if entry.source == a else B_TO_A


def _haystack(row: dict, fields: tuple[str, ...]) -> str:
    return " ".join(str(row.get(f) or "") for f in fields).lower()


def _order(row: dict, *fields: str) -> tuple[str, ...]:
    return tuple(str(row.get(f) or "") for f in fields)


def _row(entry: _Entry, direction: str, origins: dict[tuple, str | None]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": entry.kind,
        "direction": direction,
        "evidence": entry.evidence,
        "link": None,
        "provider_origin": None,
        "consumer_origin": None,
        "package_dep": None,
        "co_change": None,
    }
    raw = entry.raw
    if entry.evidence == "contract":
        out["link"] = link_row(raw)
        out["provider_origin"] = origins.get(
            ("provider", raw.get("provider_repo"), raw.get("provider_file"), raw.get("contract_id"))
        )
        out["consumer_origin"] = origins.get(
            ("consumer", raw.get("consumer_repo"), raw.get("consumer_file"), _consumer_id(raw))
        )
    elif entry.evidence == "package":
        out["package_dep"] = {f: raw[f] for f in _PACKAGE_DEP_FIELDS if raw.get(f) is not None}
    else:
        out["co_change"] = {f: raw[f] for f in _CO_CHANGE_FIELDS if raw.get(f) is not None}
    return out
