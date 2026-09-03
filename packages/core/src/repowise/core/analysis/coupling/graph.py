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
  code dependency. ``strength`` is the indexer's decay-weighted score, surfaced
  verbatim; ``support`` is the plain number of shared commits behind it.
* ``confidence_ab`` and ``confidence_ba`` are read off the two files' own commit
  totals, so they can disagree: a file that never changes without its partner
  is a different finding from two files that merely change often. Both come
  from the same walk as ``support``, so the ratio is not mixing populations.
* ``structural`` says whether the dependency graph explains the pair. Absent
  for a file the parser never ingested, where there is no edge to look for.
* We do **not** fabricate a "strengthening / weakening" trend: co-change history
  is not snapshotted, so a trend is not derivable. ``strength`` (magnitude) and
  ``last_co_change`` (recency) are the only honest encodings.
* Only files that actually participate in a coupling appear as nodes. An
  isolated file has nothing to say on this surface, so it is omitted rather than
  drawn as a lonely dot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...co_change import canonical_pair, parse_partners


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
    from the indexer; not a percentage). ``support`` is how many commits touched
    both. ``confidence_ab`` is the share of ``source``'s commits that also
    touched ``target``, and ``confidence_ba`` the reverse; either is ``None``
    when the commit total is unknown. ``last_co_change`` is the ISO date of the
    most recent shared commit, or ``None`` if unknown. ``dependency_kind`` is
    the graph edge behind a ``corroborated`` verdict, and ``None`` otherwise.
    """

    source: str
    target: str
    strength: float
    last_co_change: str | None
    support: int = 0
    confidence_ab: float | None = None
    confidence_ba: float | None = None
    structural: str | None = None
    dependency_kind: str | None = None


@dataclass(frozen=True)
class _Pair:
    """One pair mid-merge, before it becomes an edge."""

    strength: float
    last: str | None
    support: int
    commits_a: int
    commits_b: int
    structural: str | None
    dependency_kind: str | None

    def merge(self, other: _Pair) -> _Pair:
        """Combine the two directions of the same pair, keeping the best of each."""
        return _Pair(
            strength=max(self.strength, other.strength),
            last=max((d for d in (self.last, other.last) if d), default=None),
            support=max(self.support, other.support),
            commits_a=max(self.commits_a, other.commits_a),
            commits_b=max(self.commits_b, other.commits_b),
            structural=self.structural or other.structural,
            dependency_kind=self.dependency_kind or other.dependency_kind,
        )


def _ratio(support: int, commits: int) -> float | None:
    """Share of *commits* that also touched the partner, or ``None`` if unknown."""
    if support <= 0 or commits <= 0:
        return None
    return round(min(support / commits, 1.0), 3)


@dataclass
class CouplingGraph:
    """The assembled graph: nodes referenced by the (possibly capped) edges."""

    nodes: list[CouplingNode]
    edges: list[CouplingEdge]
    total_edges: int
    #: Distinct files appearing in at least one pre-cap pair, over the files
    #: with any commit history. Gives ``total_edges`` a scale: 14,115
    #: couplings across 300 files and across 3,000 files describe different
    #: repositories.
    coupled_files: int = 0
    total_files: int = 0


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
    N of M" line, ``coupled_files`` the distinct files those pairs span, and ``total_files``
    how many files were considered at all. Only files referenced by a kept edge become nodes.
    """
    # Deduplicate symmetric partner records into undirected edges. Each side
    # records the pair from its own vantage point, so keep whichever is
    # strongest and carry both files' commit totals off it.
    best: dict[tuple[str, str], _Pair] = {}
    for src, meta in git_meta_by_path.items():
        for partner in parse_partners(meta.co_change_partners_json):
            dst = partner.file_path
            if dst == src or partner.weight <= 0:
                continue
            key = canonical_pair(src, dst)
            # Orient the record's two commit totals onto the canonical pair.
            forward = src == key[0]
            seen = _Pair(
                strength=partner.weight,
                last=partner.last_co_change,
                support=partner.support,
                commits_a=partner.self_commits if forward else partner.partner_commits,
                commits_b=partner.partner_commits if forward else partner.self_commits,
                structural=partner.structural,
                dependency_kind=partner.dependency_kind,
            )
            prev = best.get(key)
            best[key] = seen if prev is None else prev.merge(seen)

    edges = [
        CouplingEdge(
            source=a,
            target=b,
            strength=round(pair.strength, 4),
            last_co_change=pair.last,
            support=pair.support,
            confidence_ab=_ratio(pair.support, pair.commits_a),
            confidence_ba=_ratio(pair.support, pair.commits_b),
            structural=pair.structural,
            dependency_kind=pair.dependency_kind,
        )
        for (a, b), pair in best.items()
    ]
    edges.sort(key=lambda e: e.strength, reverse=True)
    total = len(edges)
    # Counted over the keys of the pre-cap dict, so it scales `total_edges`
    # rather than the capped slice below it.
    coupled_files = len({path for key in best for path in key})
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

    return CouplingGraph(
        nodes=nodes,
        edges=edges,
        total_edges=total,
        coupled_files=coupled_files,
        # Every file the git walk recorded, coupled or not -- the denominator
        # the coupled count is a share of.
        total_files=len(git_meta_by_path),
    )
