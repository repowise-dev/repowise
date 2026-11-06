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
Currency = Literal["current", "stale", "absent"]

#: Oldest analyzer version whose stored delta a row still shows, flagged stale.
#: Rows below it come from the change-health model before its per-commit store
#: was rebuilt (the v21-era rows long-lived indexes still hold); their counts
#: are not readable next to today's, so they read as never scanned. Raise it
#: when a version bump changes what a finding means.
MIN_SHOWN_ANALYZER_VERSION = 40

#: Values the ``delta`` filter accepts. ``improved`` and ``worsened`` each
#: include ``mixed``: a change that fixed three things and broke one did make
#: something better, and did make something worse.
DELTA_FILTERS: tuple[str, ...] = ("improved", "worsened", "mixed", "neutral")


@dataclass(frozen=True, slots=True)
class DeltaStamp:
    """The analyzer a stored delta was computed by. Counts from two stamps never
    add up: every aggregate reads rows with the current stamp only."""

    analyzer_version: int
    rules_fingerprint: str
    performance_model_version: int

    @classmethod
    def of(cls, row: Any) -> DeltaStamp:
        return cls(
            int(row.analyzer_version or 0),
            row.rules_fingerprint or "",
            int(row.performance_model_version or 0),
        )


def delta_currency(row: Any, current: DeltaStamp) -> Currency:
    """``current`` when *row* has the current stamp; ``stale`` when an older but
    still readable analyzer wrote it (its sign holds, its magnitude does not
    compare); ``absent`` below :data:`MIN_SHOWN_ANALYZER_VERSION`."""
    if DeltaStamp.of(row) == current:
        return "current"
    if int(row.analyzer_version or 0) >= MIN_SHOWN_ANALYZER_VERSION:
        return "stale"
    return "absent"


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
    without going away, and ``moved`` the findings carried to another file
    with their code (never counted as introduced). Both are ``None`` on a
    stale row, where the stored 0 means unknown. ``files``
    holds per-file score shifts; the stored rows never carry them (they are
    live-only, in ``get_change_risk``), so it stays empty for stored deltas.
    ``stale`` marks a row an older analyzer wrote: its direction holds, its
    counts do not compare with current rows.
    """

    status: str
    introduced: int
    worsened: int
    resolved: int
    improved: int | None = None
    moved: int | None = None
    files: tuple[FileScoreShift, ...] = ()
    stale: bool = False

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


def summarize_delta(row: Any, *, stale: bool = False) -> HealthDeltaSummary:
    """A stored ``GitCommitHealthDelta`` row as a :class:`HealthDeltaSummary`.

    The one reader of stored rows. ``improved_count`` and ``moved_count`` are
    read only when not *stale*: an older analyzer's row stores 0 there, which
    means unknown, not zero.
    """
    return HealthDeltaSummary(
        status=row.status,
        introduced=int(row.introduced_count or 0),
        worsened=int(row.worsened_count or 0),
        resolved=int(row.resolved_count or 0),
        improved=None if stale else int(row.improved_count or 0),
        moved=None if stale else int(row.moved_count or 0),
        stale=stale,
    )
