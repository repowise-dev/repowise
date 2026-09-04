"""Worst-first ordering of per-file health metrics.

One comparator, so no two surfaces can disagree about which file is worst.
``score`` alone cannot rank the band that matters: it clamps at
:data:`~repowise.core.analysis.health.scoring.SCORE_FLOOR`, so on a real repo
dozens of files tie there and a list sorted on score alone comes back in
whatever order its source happened to produce.

Every function here reads its rows through :func:`..rows.field`, so a mapping,
an analyzer dataclass and an ORM row all rank identically. Nothing here loads
persistence, a session, or the analyzer engine.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .rows import field
from .scoring import SCORE_MAX

__all__ = [
    "deduction_by_path",
    "sort_metrics_worst_first",
    "worst_first_key",
    "worst_metric",
]


def deduction_by_path(findings: Iterable[Any]) -> dict[str, float]:
    """``{file_path: summed health_impact}`` over already-loaded findings.

    The in-memory twin of the persistence layer's grouped ``SUM`` aggregate, for
    callers that hold the findings anyway and must not pay a second query.
    ``health_impact`` is the applied (category-capped) contribution the scorer
    used, so the sum equals the breakdown endpoint's ``total_deduction``.

    Findings are summed as given: filtering to a status is the caller's, since
    a caller that already narrowed its rows must not be narrowed again.
    """
    totals: dict[str, float] = {}
    for finding in findings:
        path = field(finding, "file_path", None)
        if not path:
            continue
        totals[path] = totals.get(path, 0.0) + float(field(finding, "health_impact", 0.0) or 0.0)
    return totals


def worst_first_key(row: Any, deductions: Mapping[str, float]) -> tuple[float, float, str]:
    """The canonical ranking key: ``(score asc, deduction desc, path asc)``.

    ``deduction`` is the pre-clamp magnitude, so it keeps ranking below the
    floor: a -25 file sorts above a -9 file that prints the same 1.0. A file
    absent from the map has no findings and so no magnitude, which is 0.0. The
    trailing path makes the order total, so a page boundary is stable across
    requests instead of shuffling two otherwise equal rows.

    *deductions* has no default. A caller that omits it does not get a slightly
    worse order, it gets the floor band tied on path — the exact bug this key
    exists to fix — and would get it silently. Pass ``{}`` to say out loud that
    there is no magnitude to rank on.
    """
    path = str(field(row, "file_path", "") or "")
    magnitude = float(deductions.get(path, 0.0))
    # An unscored row reads as a perfect score rather than the worst one: a
    # missing measurement is not evidence of a problem.
    score = field(row, "score", SCORE_MAX)
    return (float(SCORE_MAX if score is None else score), -magnitude, path)


def sort_metrics_worst_first(rows: Sequence[Any], deductions: Mapping[str, float]) -> list[Any]:
    """Order per-file metrics worst-first. Pure: the input list is untouched."""
    return sorted(rows, key=lambda row: worst_first_key(row, deductions))


def worst_metric(rows: Iterable[Any], deductions: Mapping[str, float]) -> Any | None:
    """The single worst row under the same key, or ``None`` for no rows.

    Identical to ``sort_metrics_worst_first(rows, deductions)[0]`` without
    sorting the tail. Sharing the key is what keeps a repo's headline "worst
    performer" from naming a different file than the worst-files list under it.
    """
    return min(rows, key=lambda row: worst_first_key(row, deductions), default=None)
