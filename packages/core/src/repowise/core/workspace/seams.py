"""Riskiest seams: service pairs ranked by a fixed heuristic over the system graph.

A seam is a pair of services joined by a cross-service edge: a structural
dependency (a contract link or a package dependency) or co-change in git
history. Its score is ``confidence x reach x (1 + undeclared co-change)``:

* confidence: the strongest structural edge's link confidence (0-1).
* reach: services a change to the depended-on side reaches over structural
  edges (the blast radius walk, :data:`.blast_radius.DEFAULT_MAX_DEPTH` hops),
  the other side of the pair included; at least 1.
* undeclared co-change: the pair's co-change strength (0-1) when no contract
  links them (a package dependency is not a contract); 0 when a contract does,
  or when history was read and no co-change was found; ``None`` when either
  repo holds too little indexed history to show co-change. The score then
  leaves the factor out and is a floor (:attr:`Seam.score_is_floor`), never
  a zero.

A pair joined by co-change alone scores ``co-change x reach``.

Uncalibrated: the score orders pairs to look at first; it is not a probability
and not comparable across workspaces. Pure; the caller hands in the graph and
how many commits each repo's index holds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from repowise.core.workspace.blast_radius import cross_repo_blast_radius
from repowise.core.workspace.system_graph import SystemGraph

#: Commits a repo's index must hold before co-change can show in its history:
#: the miner needs two working sessions, so one commit can never show it.
MIN_HISTORY_COMMITS = 2


@dataclass
class Seam:
    """One service pair and every input its score was computed from."""

    services: tuple[str, str]  # node ids; the depended-on side second when there is one
    direction: str  # "->" (first depends on second), "<->" (both ways) or "~" (co-change only)
    kinds: list[str]  # structural edge kinds between the pair
    confidence: float | None  # strongest structural link; None when co-change only
    reach: int
    co_change: float | None  # co-change strength; None when it could not be measured
    declared: bool  # a contract links the pair
    score: float | None  # None when the pair has no input left to score
    #: Why an input is missing, e.g. "co-change not measured: cart has 1 commit indexed".
    missing: list[str] = field(default_factory=list)

    @property
    def undeclared(self) -> float | None:
        """Co-change no contract explains: the score's third factor."""
        if self.declared:
            return 0.0  # a contract explains whatever co-change there is
        return self.co_change

    @property
    def score_is_floor(self) -> bool:
        """The score left out a factor that could only raise it (co-change not measured)."""
        return self.score is not None and self.confidence is not None and self.undeclared is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "services": list(self.services),
            "direction": self.direction,
            "kinds": self.kinds,
            "confidence": self.confidence,
            "reach": self.reach,
            "co_change": self.co_change,
            "declared": self.declared,
            "undeclared_co_change": self.undeclared,
            "score": self.score,
            "score_is_floor": self.score_is_floor,
            "missing": self.missing,
        }


def service_reach(graph: SystemGraph) -> dict[str, int]:
    """``node id -> services a change to it reaches`` over structural edges only."""
    return {
        n.id: cross_repo_blast_radius(graph, [n.id], include_behavioral=False).total_impacted
        for n in graph.nodes
    }


def rank_seams(
    graph: SystemGraph,
    commits: Mapping[str, int] | None = None,
    *,
    reach: Mapping[str, int] | None = None,
) -> list[Seam]:
    """Every cross-service pair in *graph*, highest score first.

    *commits* maps a repo alias to the commits its index holds; a repo absent
    from it (or *commits* None) has unknown history. *reach* is
    :func:`service_reach`, passed when the caller already computed it.
    """
    reach = reach if reach is not None else service_reach(graph)
    repo_of = {n.id: n.repo for n in graph.nodes}
    pairs: dict[frozenset[str], dict[str, Any]] = {}
    for e in graph.edges:
        if e.source == e.target:
            continue
        p = pairs.setdefault(
            frozenset((e.source, e.target)), {"deps": set(), "kinds": set(), "conf": None, "cc": None}
        )
        if e.structural:
            p["deps"].add((e.source, e.target))
            p["kinds"].add(e.kind)
            p["conf"] = max(p["conf"] or 0.0, e.confidence)
        else:
            p["cc"] = max(p["cc"] or 0.0, e.confidence)
    seams = [_seam(key, p, reach, repo_of, commits) for key, p in pairs.items()]
    return sorted(
        seams, key=lambda s: (s.score is None, -(s.score or 0.0), -s.reach, s.services)
    )


def _seam(
    key: frozenset[str],
    p: dict[str, Any],
    reach: Mapping[str, int],
    repo_of: Mapping[str, str],
    commits: Mapping[str, int] | None,
) -> Seam:
    deps: set[tuple[str, str]] = p["deps"]
    if len(deps) == 1:
        (consumer, provider), direction = next(iter(deps)), "->"
        depended = [provider]
    else:
        consumer, provider = sorted(key)
        direction = "<->" if deps else "~"
        depended = [consumer, provider]
    seam_reach = max(1, *(reach.get(n, 0) for n in depended))
    missing: list[str] = []
    co_change = p["cc"]
    declared = any(k != "package" for k in p["kinds"])
    if co_change is None and not declared:
        short = dict.fromkeys(
            repo
            for repo in (repo_of.get(consumer, consumer), repo_of.get(provider, provider))
            if (commits or {}).get(repo, 0) < MIN_HISTORY_COMMITS
        )
        missing.extend(f"co-change not measured: {r} has {_commits(commits, r)}" for r in short)
        if not short:
            co_change = 0.0
    seam = Seam(
        services=(consumer, provider),
        direction=direction,
        kinds=sorted(p["kinds"]),
        confidence=p["conf"],
        reach=seam_reach,
        co_change=co_change,
        declared=declared,
        score=None,
        missing=missing,
    )
    undeclared = seam.undeclared
    if seam.confidence is not None:
        seam.score = round(seam.confidence * seam_reach * (1.0 + (undeclared or 0.0)), 2)
    elif undeclared is not None:
        seam.score = round(undeclared * seam_reach, 2)
    return seam


def _commits(commits: Mapping[str, int] | None, repo: str) -> str:
    if commits is None or repo not in commits:
        return "no indexed history"
    n = commits[repo]
    return f"{n} commit{'' if n == 1 else 's'} indexed"


__all__ = ["MIN_HISTORY_COMMITS", "Seam", "rank_seams", "service_reach"]
