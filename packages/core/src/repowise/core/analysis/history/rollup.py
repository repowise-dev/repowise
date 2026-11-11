"""Roll stored commit deltas up to a change, and changes up to a period.

Counts only add: a change's introduced count is the sum of its commits'. Two
commits in one change that introduce and then resolve the same finding both
count, which is what happened on the branch. A squash change has one commit,
so its rollup is that commit's delta exactly; merge commits themselves are
never scanned (the commit walk skips merges), so a merge change sums its
branch commits.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from .delta import DeltaKind, HealthDeltaSummary, delta_kind


class _Counted(Protocol):
    @property
    def kind(self) -> DeltaKind: ...

    introduced: int
    worsened: int
    resolved: int
    improved: int | None


@dataclass(frozen=True, slots=True)
class HealthRollup:
    """What a group of commits did to health, summed.

    ``files_worsened`` counts distinct paths named by the stored worse
    findings (capped per commit at scan time, so a floor) plus files whose
    stored score fell. ``files_improved`` needs stored per-file scores and is
    ``None`` until delta rows carry them; resolved findings are stored as a
    count without paths, so they cannot name a file.
    """

    status: str
    scanned_commits: int
    unscanned_commits: int
    introduced: int
    worsened: int
    resolved: int
    improved: int | None
    moved: int | None
    files_improved: int | None
    files_worsened: int

    @property
    def kind(self) -> DeltaKind:
        return delta_kind(self.resolved + (self.improved or 0), self.introduced + self.worsened)


def _net_score_change(deltas: Sequence[HealthDeltaSummary]) -> dict[str, float]:
    """``path -> summed score change`` over the deltas' stored per-file shifts."""
    net: dict[str, float] = {}
    for shift in (s for d in deltas for s in d.files if s.change is not None):
        net[shift.path] = net.get(shift.path, 0.0) + shift.change
    return net


def rollup_deltas(
    deltas: Sequence[HealthDeltaSummary | None], worse_paths: Iterable[str] = ()
) -> HealthRollup | None:
    """Sum the deltas of one change's commits; ``None`` when none was scanned.

    *deltas* has one entry per commit, ``None`` for a commit never scanned.
    *worse_paths* are the paths the commits' stored worse findings name.
    """
    scanned = [d for d in deltas if d is not None]
    if not scanned:
        return None
    unscanned = len(deltas) - len(scanned)
    improved_known = [d.improved for d in scanned if d.improved is not None]
    moved_known = [d.moved for d in scanned if d.moved is not None]
    net_by_path = _net_score_change(scanned)
    falling = {p for p, net in net_by_path.items() if net < 0}
    complete = unscanned == 0 and all(d.status == "available" for d in scanned)
    return HealthRollup(
        status="available" if complete else "partial",
        scanned_commits=len(scanned),
        unscanned_commits=unscanned,
        introduced=sum(d.introduced for d in scanned),
        worsened=sum(d.worsened for d in scanned),
        resolved=sum(d.resolved for d in scanned),
        improved=sum(improved_known) if improved_known else None,
        moved=sum(moved_known) if moved_known else None,
        files_improved=(sum(1 for net in net_by_path.values() if net > 0) if net_by_path else None),
        files_worsened=len(set(worse_paths) | falling),
    )


@dataclass(frozen=True, slots=True)
class PeriodHealthSummary:
    """A period's headline: how many items made health better, worse, both or neither.

    Improvements lead the field order on purpose: the headline reads them
    first. ``net_findings`` is findings made better minus findings made worse.
    """

    total: int
    improved: int
    worsened: int
    mixed: int
    neutral: int
    unscanned: int
    findings_resolved: int
    findings_improved: int | None
    findings_introduced: int
    findings_worsened: int
    net_findings: int


def summarize_period(items: Iterable[_Counted | None]) -> PeriodHealthSummary:
    """Count *items* (commit deltas or change rollups; ``None`` = never scanned)."""
    kinds = {"improved": 0, "worsened": 0, "mixed": 0, "neutral": 0}
    total = unscanned = resolved = introduced = worsened = 0
    improved_known: list[int] = []
    for item in items:
        total += 1
        if item is None:
            unscanned += 1
            continue
        kinds[item.kind] += 1
        resolved += item.resolved
        introduced += item.introduced
        worsened += item.worsened
        if item.improved is not None:
            improved_known.append(item.improved)
    improved = sum(improved_known) if improved_known else None
    return PeriodHealthSummary(
        total=total,
        unscanned=unscanned,
        findings_resolved=resolved,
        findings_improved=improved,
        findings_introduced=introduced,
        findings_worsened=worsened,
        net_findings=resolved + (improved or 0) - introduced - worsened,
        **kinds,
    )
