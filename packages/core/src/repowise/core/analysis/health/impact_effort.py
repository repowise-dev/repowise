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
rows, this module only places them. :func:`work_queue_targets` builds the rows
the plane and the health work queue share, one per file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from repowise.core.analysis.health.effort import EFFORT_WEIGHT, effort_bucket
from repowise.core.analysis.health.models import primary_finding, split_by_origin
from repowise.core.analysis.health.queue.eligibility import MIN_WORTH
from repowise.core.analysis.health.queue.order import TIER_RANK
from repowise.core.analysis.health.rows import detail_map, field
from repowise.core.analysis.health.suggestions import suggestion_for

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


# Not ``aggregation.SEVERITY_ORDER``: there an unknown severity sorts last, here it counts as low.
_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

#: The work queue's orders over :func:`work_queue_targets` rows.
WORK_QUEUE_SORTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "impact_per_effort": lambda t: (-t["impact_per_effort"], -t["total_impact"]),
    "total_impact": lambda t: -t["total_impact"],
    # ``score`` clamps at 1.0, so on a real repo dozens of targets tie at the
    # top and sorting on it alone returns them in dict-insertion order.
    # ``total_impact`` is the same pre-clamp deduction magnitude the crud layer
    # ranks metrics by — but summed over the findings that survived this
    # request's finding-level filters (biomarker, severity, min_severity,
    # dimension, status), so under a filter this ranks by the filtered depth
    # rather than the file's full depth. That is what a filtered queue should
    # do; it just means the order is not expected to match /health/files once
    # a filter is on.
    "score": lambda t: (t["score"], -t["total_impact"], t["file_path"]),
    "finding_count": lambda t: -t["finding_count"],
}


def _finding_filter(
    biomarker: str | None, severity: str | None, min_severity: str | None
) -> Callable[[Any], bool]:
    """One marker, then exact severities or else a severity floor."""
    # An all-empty list (","; " ") means the caller selected nothing, not that
    # nothing matches — falling through to ``min_severity`` keeps a stray
    # serialization from silently emptying the queue behind a 200.
    picked = {v.strip().lower() for v in (severity or "").split(",") if v.strip()}
    floor = _SEVERITY_ORDER.get(min_severity, 0) if min_severity else None

    def keep(f: Any) -> bool:
        if biomarker and f.biomarker_type != biomarker:
            return False
        if picked:
            return (f.severity or "").lower() in picked
        if floor is not None:
            return _SEVERITY_ORDER.get(f.severity, 0) >= floor
        return True

    return keep


def _lead_finding(fs: list[Any]) -> Any:
    primary = primary_finding(fs)
    if primary is not None:
        return primary
    # Every finding here is advisory, so no cause accuses this file and the
    # general queue never reaches this: advisory is excluded from it. A caller
    # who filtered to an advisory marker did reach it, and the marker they
    # asked for is the honest lead for the row.
    return max(fs, key=lambda x: (_SEVERITY_ORDER.get(x.severity, 0), -(x.line_start or 0)))


def _target_row(file_path: str, fs: list[Any], m: Any, bucket: str) -> dict:
    primary = _lead_finding(fs)
    # Impact, and therefore the ranking, counts only findings still open:
    # ``score`` on this row was computed from open findings, and a file
    # whose findings were all dismissed is not work to rank near the top.
    open_fs = [x for x in fs if (getattr(x, "status", None) or "open") == "open"]
    total_impact = round(sum(x.health_impact for x in open_fs), 3)
    return {
        "file_path": file_path,
        "score": round(m.score, 2),
        "nloc": m.nloc,
        "module": m.module or None,
        "is_test": bool(getattr(m, "is_test", False)),
        "primary_biomarker": primary.biomarker_type,
        "primary_severity": primary.severity,
        "primary_reason": primary.reason,
        "primary_function": primary.function_name,
        "primary_line_start": primary.line_start,
        "primary_line_end": primary.line_end,
        "primary_suggestion": suggestion_for(primary.biomarker_type),
        "primary_finding_id": primary.id,
        "total_impact": total_impact,
        "finding_count": len(fs),
        "open_finding_count": len(open_fs),
        "biomarkers": sorted({x.biomarker_type for x in fs}),
        "effort_bucket": bucket,
        "impact_per_effort": round(total_impact / EFFORT_WEIGHT[bucket], 3),
    }


def work_queue_targets(
    metric_by_path: Mapping[str, Any],
    findings: Iterable[Any],
    *,
    biomarker: str | None = None,
    severity: str | None = None,
    min_severity: str | None = None,
    max_effort: str | None = None,
    history: str = "exclude",
) -> tuple[list[dict], int]:
    """One row per file the finding filters keep, unsorted, and the history-only count.

    *metric_by_path* holds only the files the caller's file-level filters keep.
    ``history="exclude"`` leaves out a file whose only findings are history
    markers, unless a marker was named.
    """
    keep = _finding_filter(biomarker, severity, min_severity)
    by_file: dict[str, list[Any]] = {}
    for f in findings:
        if keep(f):
            by_file.setdefault(f.file_path, []).append(f)
    max_effort_rank = EFFORT_WEIGHT.get(max_effort or "", 99)

    targets: list[dict] = []
    history_only = 0
    for file_path, fs in by_file.items():
        m = metric_by_path.get(file_path)
        # Absent means either filtered out above, or a file this reading
        # cannot score: under ``code_shape``, a row with no recorded split.
        # Ranking that on a stand-in 10.0 would put an unmeasured file at the
        # top of a list ordered by how bad things are. The same holds for a
        # file in a language health has no dialect for, stored with no score.
        if m is None or m.score is None:
            continue
        # Naming a marker reaches it whatever its origin, as it reaches the
        # zero-impact dimensions when the caller reads the findings.
        if history == "exclude" and biomarker is None and not split_by_origin(fs)[0]:
            history_only += 1
            continue
        bucket = effort_bucket(m.nloc)
        if EFFORT_WEIGHT[bucket] > max_effort_rank:
            continue
        targets.append(_target_row(file_path, fs, m, bucket))
    return targets, history_only


__all__ = [
    "EFFORT_MIDLINE_LINES",
    "GAIN_MIDLINE_POINTS",
    "PLOT_CAP",
    "WORK_QUEUE_SORTS",
    "ImpactEffortPlane",
    "ImpactEffortPoint",
    "build_impact_effort",
    "fix_first_marks",
    "plan_lines",
    "work_queue_targets",
]
