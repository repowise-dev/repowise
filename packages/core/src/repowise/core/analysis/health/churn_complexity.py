"""Churn x complexity scatter points (the "hotspot anatomy" view).

Phase 4 of health surfacing: plot every recently-changed file by how often it
changes (churn) against how tangled it is (complexity). The top-right corner is
the danger zone -- files that are both volatile and complex are where defects
concentrate and where refactoring pays off most. Pure surfacing: every input
is already computed and persisted (churn from the git indexer, complexity from
the walker, score from the health engine); no recompute, no new measurement,
no LLM.

State-free like :mod:`signals.py` and :mod:`trends.py` so the join logic stays
unit-testable without a DB -- callers pass already-loaded rows and get plain
dataclasses back. The same :func:`churn_complexity_points` assembler backs the
REST endpoint today and any future export.

Honesty rule: a file only earns a point when it has *recent churn* to speak of
(``commit_count_90d > 0``). A file with no recent commits has nothing to say on
the churn axis -- plotting it on the y-axis would imply a story that isn't
there -- so it is omitted rather than pinned to zero. Complexity is never used
to filter: a high-churn, low-complexity file is a real and useful bottom-right
signal ("changes constantly but stays simple").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class MetricLike(Protocol):
    """The health-metric fields the quadrant reads (duck-typed).

    Matches ``persistence.models.HealthFileMetric``; a Protocol so the
    assembler stays free of any ORM import and tests can pass a stub.
    """

    file_path: str
    score: float
    max_ccn: int
    nloc: int


class GitMetaLike(Protocol):
    """The churn fields the quadrant reads (duck-typed)."""

    commit_count_90d: int | None
    churn_percentile: float | None


@dataclass
class ChurnComplexityPoint:
    """One file positioned in the churn x complexity plane.

    ``commit_count_90d`` is the x (churn) axis, ``max_ccn`` the y (complexity)
    axis, ``nloc`` encodes dot size, and ``score`` drives dot color via the
    health band. ``churn_percentile`` (0-100) is repo-relative context shown in
    the tooltip so a raw count is interpretable across repos of any size.
    """

    file_path: str
    commit_count_90d: int
    max_ccn: int
    nloc: int
    score: float
    churn_percentile: float


def _churn_pct(raw: float | None) -> float:
    """Normalize the stored churn percentile to a 0-100 scale.

    The column is stored 0-1; tolerate a 0-100 caller defensively (mirrors
    ``churn-vs-bus-factor-scatter`` on the UI side). Absent -> 0.0 (the file is
    simply un-ranked, not "no signal" -- the count axis carries the real data).
    """
    if raw is None:
        return 0.0
    return round(raw * 100.0, 1) if raw <= 1 else round(raw, 1)


def churn_complexity_points(
    metrics: list[MetricLike],
    git_meta_by_path: dict[str, GitMetaLike],
) -> list[ChurnComplexityPoint]:
    """Assemble churn x complexity points from already-loaded rows.

    *metrics* are the repo's ``HealthFileMetric`` rows; *git_meta_by_path* maps
    ``file_path`` to its ``GitMetadata`` row (from ``get_all_git_metadata``).
    No DB access and no recompute -- a plain join keyed on ``file_path``.

    Returns the points sorted by the "danger product" (churn x complexity)
    descending so a caller that caps the list keeps the most consequential
    files. Files with no recent churn are omitted (see module docstring).
    """
    points: list[ChurnComplexityPoint] = []
    for m in metrics:
        g = git_meta_by_path.get(m.file_path)
        commit_count = (g.commit_count_90d or 0) if g else 0
        if commit_count <= 0:
            continue
        max_ccn = m.max_ccn or 0
        points.append(
            ChurnComplexityPoint(
                file_path=m.file_path,
                commit_count_90d=commit_count,
                max_ccn=max_ccn,
                nloc=m.nloc or 0,
                score=round(m.score, 2),
                churn_percentile=_churn_pct(g.churn_percentile if g else None),
            )
        )
    points.sort(key=lambda p: (p.commit_count_90d * p.max_ccn, p.commit_count_90d), reverse=True)
    return points
