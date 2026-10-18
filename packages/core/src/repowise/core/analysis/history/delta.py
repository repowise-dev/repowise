"""One commit's stored health delta, read as better or worse.

The comparison itself belongs to :mod:`repowise.core.analysis.change_health`
and is stored per commit at index time (``git_commit_health_deltas``). This
module only reads those rows back into the better/worse vocabulary the history
surfaces lead with; it never re-derives a finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

DeltaKind = Literal["improved", "worsened", "mixed", "neutral"]

#: Values the ``delta`` filter accepts. ``improved`` and ``worsened`` each
#: include ``mixed``: a change that fixed three things and broke one did make
#: something better, and did make something worse.
DELTA_FILTERS: tuple[str, ...] = ("improved", "worsened", "mixed", "neutral")


@dataclass(frozen=True, slots=True)
class FileScoreShift:
    """A file's health score before and after a change; ``None`` when unscored."""

    path: str
    before: float | None
    after: float | None

    @property
    def change(self) -> float | None:
        if self.before is None or self.after is None:
            return None
        return round(self.after - self.before, 2)


@dataclass(frozen=True, slots=True)
class HealthDeltaSummary:
    """The counts behind one commit's delta, and which way it went.

    ``improved`` is the analyzer's count of findings that measure better
    without going away; ``None`` until delta rows store it. ``files`` holds
    per-file score shifts, empty until delta rows store them (the analyzer's
    ``health_delta.files[]``); both are the seam for that data, read through
    :func:`summarize_delta` so no caller changes when it lands.
    """

    status: str
    introduced: int
    worsened: int
    resolved: int
    improved: int | None = None
    files: tuple[FileScoreShift, ...] = ()

    @property
    def better(self) -> int:
        return self.resolved + (self.improved or 0)

    @property
    def worse(self) -> int:
        return self.introduced + self.worsened

    @property
    def kind(self) -> DeltaKind:
        return delta_kind(self.better, self.worse)


def delta_kind(better: int, worse: int) -> DeltaKind:
    """``improved`` / ``worsened`` / ``mixed`` / ``neutral`` from the two sides."""
    if better and worse:
        return "mixed"
    if better:
        return "improved"
    if worse:
        return "worsened"
    return "neutral"


def matches_delta(kind: DeltaKind, wanted: str | None) -> bool:
    """Whether a delta of *kind* passes the ``delta`` filter *wanted*."""
    if not wanted:
        return True
    if wanted in ("improved", "worsened"):
        return kind in (wanted, "mixed")
    return kind == wanted


def summarize_delta(row: Any) -> HealthDeltaSummary:
    """A stored ``GitCommitHealthDelta`` row as a :class:`HealthDeltaSummary`."""
    return HealthDeltaSummary(
        status=row.status,
        introduced=int(row.introduced_count or 0),
        worsened=int(row.worsened_count or 0),
        resolved=int(row.resolved_count or 0),
    )
