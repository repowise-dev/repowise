"""Better/worse history shapes: commit and change health deltas, period
headlines and wins. Twins of :mod:`repowise.core.analysis.history`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

DeltaKindLiteral = Literal["improved", "worsened", "mixed", "neutral"]


class FileScoreShiftResponse(BaseModel):
    """A file's health score before and after; ``None`` on a side it was unscored."""

    path: str
    before: float | None = None
    after: float | None = None
    change: float | None = None


class HealthDeltaSummaryResponse(BaseModel):
    """What one commit did to health, as list rows carry it.

    ``kind`` reads the counts: ``improved`` when it only made things better,
    ``worsened`` when it only made them worse, ``mixed`` for both, ``neutral``
    for neither. ``improved`` (findings that measure better) and ``moved`` are
    null on a stale row, where the stored 0 means unknown; ``files`` stays empty for stored deltas (per-file scores are
    live-only). ``stale`` means an older analyzer wrote it: the direction holds,
    the counts do not compare with current rows, and no aggregate counts it.
    """

    status: str
    kind: DeltaKindLiteral
    introduced: int
    worsened: int
    resolved: int
    improved: int | None = None
    #: Findings carried to another file with their code; never introduced.
    moved: int | None = None
    stale: bool = False
    files: list[FileScoreShiftResponse] = []


class HealthRollupResponse(BaseModel):
    """What a change's commits did to health, summed.

    ``status`` is ``partial`` when a commit was not scanned or skipped files.
    Only rows from the current analyzer count; the rest are unscanned.
    ``files_worsened`` counts paths the stored worse findings name (a floor:
    findings are capped per commit). ``files_improved`` is null until stored
    deltas carry per-file scores.
    """

    status: str
    kind: DeltaKindLiteral
    scanned_commits: int
    unscanned_commits: int
    introduced: int
    worsened: int
    resolved: int
    improved: int | None = None
    moved: int | None = None
    files_improved: int | None = None
    files_worsened: int


class PeriodHealthSummaryResponse(BaseModel):
    """A period headline: items that made health better first, then worse.

    Items are changes on ``/changes/summary`` and commits on
    ``/commits/health-summary``. ``unscanned`` items have no delta from the
    current analyzer ("not yet analysed") and are in none of the four kinds;
    recent commits refill as updates rescan them.
    """

    since: datetime | None = None
    until: datetime | None = None
    total: int
    improved: int
    mixed: int
    worsened: int
    neutral: int
    unscanned: int
    findings_resolved: int
    findings_improved: int | None = None
    findings_introduced: int
    findings_worsened: int
    net_findings: int


class WinsResolvingCommit(BaseModel):
    sha: str
    subject: str
    author_name: str
    committed_at: datetime | None = None
    resolved: int


class WinsFindingsResolved(BaseModel):
    """Findings commits in the window resolved. Resolved findings are stored as
    per-commit counts, so the top items are the commits that resolved most."""

    count: int
    commits: int
    top_commits: list[WinsResolvingCommit] = []


class WinsFileGain(BaseModel):
    path: str
    before: float
    after: float
    gain: float


class WinsFilesImproved(BaseModel):
    """Files commits in the window touched whose score rose between the snapshot
    at ``baseline_at`` and the one at ``current_at``."""

    count: int
    baseline_at: datetime | None = None
    current_at: datetime | None = None
    top: list[WinsFileGain] = []


class WinsRefactoring(BaseModel):
    """A refactoring plan that landed: its target is gone or shrank as planned."""

    file_path: str
    target_symbol: str
    refactoring_type: str
    resolved_at: datetime | None = None
    before_ccn: int | None = None
    after_ccn: int | None = None
    before_nloc: int | None = None
    after_nloc: int | None = None


class WinsRefactorings(BaseModel):
    count: int
    top: list[WinsRefactoring] = []


class WinsResponse(BaseModel):
    """What got better since ``since``. Positive only.

    With ``author`` every section narrows to that person's commits and the
    files they touched. ``unavailable`` names wins the index cannot answer
    from stored data (no history is kept for them).
    """

    since: datetime
    until: datetime | None = None
    author: str | None = None
    findings_resolved: WinsFindingsResolved
    files_improved: WinsFilesImproved
    refactorings_landed: WinsRefactorings
    unavailable: list[str] = []
