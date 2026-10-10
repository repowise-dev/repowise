"""The impact / effort plane over a whole filtered set of files.

One point per file: how many lines the fix touches (x) against how much health
it recovers (y). A file with a refactoring plan that recovers something is
placed by the plan, so both coordinates describe the same change: the lines its
steps span and the health it credits. A file with no such plan is placed by its
own size in code lines (NLOC) and the deduction its open findings carry, since
fixing those findings is the only change on offer. ``effort_basis`` says which.

The quadrant midlines are fixed constants, not the data's halfway points, so a
filter or a new index never moves a file from one quadrant to another unless
the file itself changed.

Pure over plain rows (``health.rows.field``); the server reads and filters the
rows, this module only places them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from repowise.core.analysis.health.queue.eligibility import MIN_WORTH
from repowise.core.analysis.health.queue.order import TIER_RANK
from repowise.core.analysis.health.rows import detail_map, field

#: The vertical midline, in lines to change. The work queue's Medium effort
#: ceiling (150 NLOC), which is also the median size of a file with findings on
#: this repository's own index (2026-10-01).
EFFORT_MIDLINE_LINES = 150
#: The horizontal midline, in health points: the least a refactoring must
#: recover to be worth a Fix-first item.
GAIN_MIDLINE_POINTS = MIN_WORTH
#: The most points one response carries. Past it, the files recovering the
#: most health are kept and ``plotted < total`` says so. Ceiling: a repository
#: with more files than this loses its lowest-gain points; the upgrade is
#: server-side binning of the tail.
PLOT_CAP = 5000

EffortBasis = Literal["plan", "file"]


@dataclass(frozen=True, slots=True)
class ImpactEffortPoint:
    file_path: str
    #: Lines the planned change spans, or the file's NLOC (``effort_basis``).
    effort_lines: int
    effort_basis: EffortBasis
    #: Health points the plan credits, or the open findings' deduction.
    recoverable_health: float
    #: The tier of the file's first Fix-first item, when it holds one.
    tier: str | None
    #: That item's place in the Fix-first list, from 1.
    fix_rank: int | None = None


@dataclass(frozen=True, slots=True)
class ImpactEffortPlane:
    points: tuple[ImpactEffortPoint, ...]
    plotted: int
    total: int
    cap: int
    effort_midline_lines: int
    gain_midline_points: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "points": [asdict(p) for p in self.points],
            "plotted": self.plotted,
            "total": self.total,
            "cap": self.cap,
            "effort_midline_lines": self.effort_midline_lines,
            "gain_midline_points": self.gain_midline_points,
        }


def plan_lines(details: Mapping[str, Any]) -> int:
    """Lines spanned by a plan's steps; 0 when no step names a span."""
    total = 0
    for step in details.get("steps") or ():
        start, end = step.get("line_start"), step.get("line_end")
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            total += end - start + 1
    return total


def fix_first_marks(items: Iterable[Any]) -> dict[str, tuple[str, int]]:
    """Each file's first Fix-first item among *items*: its tier and 1-based rank.

    *items* are ``FixItem`` objects or their dicts, ``rank`` 0-based as the
    queue writes it. A file holding several items is marked by the highest.
    """
    out: dict[str, tuple[str, int]] = {}
    for item in items:
        target = field(item, "target")
        path = field(target, "file_path") if target is not None else None
        tier = field(item, "tier")
        rank = field(item, "rank")
        if not path or tier not in TIER_RANK or not isinstance(rank, int):
            continue
        if path not in out or rank + 1 < out[path][1]:
            out[path] = (tier, rank + 1)
    return out


def build_impact_effort(
    files: Iterable[Any],
    opportunities: Iterable[Any] = (),
    marks: Mapping[str, tuple[str, int]] | None = None,
    *,
    cap: int = PLOT_CAP,
) -> ImpactEffortPlane:
    """Place every file in *files* on the plane.

    *files* rows carry ``file_path``, ``nloc`` and ``total_impact`` (the open
    deduction under the caller's filters); the caller has already left out
    history-only files. *opportunities* are open refactoring opportunity rows
    (``file_path``, ``recoverable_health``, ``details``/``details_json``).
    *marks* maps a path to its Fix-first ``(tier, rank)`` where it holds an item
    (:func:`fix_first_marks`).
    """
    plans: dict[str, tuple[int, float]] = {}
    for row in opportunities:
        gain = float(field(row, "recoverable_health") or 0.0)
        lines = plan_lines(detail_map(row))
        # A plan that recovers nothing, or names no span, is not the change
        # that fixes this file's findings; the file speaks for itself.
        if gain > 0 and lines > 0:
            plans[field(row, "file_path")] = (lines, gain)

    marks = marks or {}
    points: list[ImpactEffortPoint] = []
    for row in files:
        path = field(row, "file_path")
        plan = plans.get(path)
        mark = marks.get(path)
        if plan is not None:
            lines, gain = plan
            basis: EffortBasis = "plan"
        else:
            lines = int(field(row, "nloc") or 0)
            gain = float(field(row, "total_impact") or 0.0)
            basis = "file"
        points.append(
            ImpactEffortPoint(
                file_path=path,
                effort_lines=max(1, lines),
                effort_basis=basis,
                recoverable_health=round(gain, 3),
                tier=mark[0] if mark else None,
                fix_rank=mark[1] if mark else None,
            )
        )

    points.sort(key=lambda p: (-p.recoverable_health, p.file_path))
    kept = points[: max(cap, 0)]
    return ImpactEffortPlane(
        points=tuple(kept),
        plotted=len(kept),
        total=len(points),
        cap=cap,
        effort_midline_lines=EFFORT_MIDLINE_LINES,
        gain_midline_points=GAIN_MIDLINE_POINTS,
    )


__all__ = [
    "EFFORT_MIDLINE_LINES",
    "GAIN_MIDLINE_POINTS",
    "PLOT_CAP",
    "ImpactEffortPlane",
    "ImpactEffortPoint",
    "build_impact_effort",
    "fix_first_marks",
    "plan_lines",
]
