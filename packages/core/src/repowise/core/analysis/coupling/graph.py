"""Repo-wide change-coupling graph (the "files that change together" view).

Promotes the per-file co-change partners the git indexer already computes
(``GitMetadata.co_change_partners_json``) into a single deduplicated,
undirected edge list for the whole repo, with each node enriched by its module
/ health score / size so the UI can group, color, and size it. Pure surfacing:
every input is already computed and persisted; no recompute, no new
measurement, no LLM.

State-free like :mod:`analysis.health.churn_complexity`, :mod:`signals`, and
:mod:`trends` so the join logic stays unit-testable without a DB -- callers
pass already-loaded rows and get plain dataclasses back. The same
:func:`coupling_graph` assembler backs the REST endpoint today and any future
export.

Honesty rules:

* Co-change is a *temporal* hint (files committed together), not a verified
  code dependency -- the strength is the decay-weighted count the indexer
  already thresholds (``>= 0.5``); we surface it verbatim and never invent one.
* We do **not** fabricate a "strengthening / weakening" trend: co-change history
  is not snapshotted, so a trend is not derivable. ``strength`` (magnitude) and
  ``last_co_change`` (recency) are the only honest encodings.
* Only files that actually participate in a coupling appear as nodes. An
  isolated file has nothing to say on this surface, so it is omitted rather than
  drawn as a lonely dot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


class MetricLike(Protocol):
    """The health-metric fields the graph reads (duck-typed).

    Matches ``persistence.models.HealthFileMetric``; a Protocol so the assembler
    stays free of any ORM import and tests can pass a stub.
    """

    file_path: str
    score: float
    nloc: int
    module: str | None


class GitMetaLike(Protocol):
    """The git fields the graph reads (duck-typed).

    ``co_change_partners_json`` is the raw Text column: a JSON list of
    ``{"file_path", "co_change_count", "last_co_change"}`` partner records
    (see ``ingestion.git_indexer.co_change``).
    """

    co_change_partners_json: str


@dataclass
class CouplingNode:
    """One file that participates in at least one coupling.

    ``module`` groups the node in the legend / table (the ring hierarchy itself
    is derived from the path on the UI side). ``score`` drives the dot's health
    band color and is ``None`` only when the file has no health metric (a rare
    non-source file with git history); the UI renders that as a neutral dot.
    ``nloc`` encodes dot size.
    """

    file_path: str
    module: str | None
    score: float | None
    nloc: int


@dataclass
class CouplingEdge:
    """One undirected coupling between two files.

    ``source``/``target`` are sorted lexicographically so the pair is stable and
    deduplicated. ``strength`` is the decay-weighted co-change count (verbatim
    from the indexer; not a percentage). ``last_co_change`` is the ISO date of
    the most recent shared commit, or ``None`` if unknown.
    """

    source: str
    target: str
    strength: float
    last_co_change: str | None


@dataclass
class CouplingGraph:
    """The assembled graph: nodes referenced by the (possibly capped) edges."""

    nodes: list[CouplingNode]
    edges: list[CouplingEdge]
    total_edges: int


def _parse_partners(raw: str | None) -> list[dict]:
    """Parse a ``co_change_partners_json`` cell, tolerating absent/bad JSON.

    Mirrors the defensive parsing in :mod:`analysis.health.trends`: a malformed
    cell yields no partners rather than raising.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def coupling_graph(
    metrics: list[MetricLike],
    git_meta_by_path: dict[str, GitMetaLike],
    *,
    limit: int = 200,
) -> CouplingGraph:
    """Assemble the repo-wide coupling graph from already-loaded rows.

    *metrics* are the repo's ``HealthFileMetric`` rows (for module / score /
    nloc enrichment); *git_meta_by_path* maps ``file_path`` to its
    ``GitMetadata`` row (from ``get_all_git_metadata``), whose
    ``co_change_partners_json`` carries the partners. No DB access and no
    recompute -- a plain join keyed on ``file_path``.

    Partners are stored symmetrically (a->b and b->a), so edges are
    deduplicated on the sorted ``(source, target)`` pair, keeping the strongest
    observed strength and the most recent date. Edges are sorted by strength
    descending and capped at *limit* so a caller keeps the most consequential
    couplings; ``total_edges`` reports the pre-cap count for an honest "showing
    N of M" line. Only files referenced by a kept edge become nodes.
    """
    # Deduplicate symmetric partner records into undirected edges.
    best: dict[tuple[str, str], tuple[float, str | None]] = {}
    for src, meta in git_meta_by_path.items():
        for partner in _parse_partners(meta.co_change_partners_json):
            dst = partner.get("file_path")
            if not dst or dst == src:
                continue
            try:
                strength = float(partner.get("co_change_count") or 0.0)
            except (ValueError, TypeError):
                continue
            if strength <= 0:
                continue
            last = partner.get("last_co_change")
            key = (src, dst) if src < dst else (dst, src)
            prev = best.get(key)
            if prev is None:
                best[key] = (strength, last)
            else:
                prev_strength, prev_last = prev
                best[key] = (
                    max(prev_strength, strength),
                    max((d for d in (prev_last, last) if d), default=None),
                )

    edges = [
        CouplingEdge(source=a, target=b, strength=round(strength, 2), last_co_change=last)
        for (a, b), (strength, last) in best.items()
    ]
    edges.sort(key=lambda e: e.strength, reverse=True)
    total = len(edges)
    edges = edges[:limit]

    # Build nodes only for files referenced by a kept edge.
    metric_by_path = {m.file_path: m for m in metrics}
    referenced: set[str] = set()
    for e in edges:
        referenced.add(e.source)
        referenced.add(e.target)

    nodes = [
        CouplingNode(
            file_path=path,
            module=(metric_by_path[path].module if path in metric_by_path else None),
            score=(round(metric_by_path[path].score, 2) if path in metric_by_path else None),
            nloc=(metric_by_path[path].nloc or 0 if path in metric_by_path else 0),
        )
        for path in sorted(referenced)
    ]

    return CouplingGraph(nodes=nodes, edges=edges, total_edges=total)
