"""Run the base-versus-head comparison over a list of commits, for storage.

Turns shas into the rows ``git_commit_health_deltas`` and
``git_commit_health_findings`` hold, so a host with no working tree can answer
for a commit the viewer picks at random, plus a ``git_commit_health_scans``
marker for each commit that stores no row, so it is not compared again.

Serial by measurement: ~0.19s per analysed file single-threaded, but 0.41s
across a thread pool, because the analyzer is GIL-bound. So it bounds itself
by a wall clock instead, leaving older commits unscanned.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from repowise.core.repo_config import health_rules_fingerprint

from .models import ChangeFinding, ChangeHealthDelta
from .service import MAX_CHANGED_FILES, ChangeHealthDeltaService, DeltaRequest
from .sources import RevisionPair

#: Findings kept per commit. The delta row carries the true totals, so a
#: capped commit still reports honestly how much it is not showing.
MAX_FINDINGS_PER_COMMIT = 50

#: Statuses worth a row. A comparison that could not run is not a clean
#: commit, and storing it would make "no findings" ambiguous forever.
_STORED_STATUSES = frozenset({"available", "partial"})

#: Statuses that may differ on a second try, so they leave no marker:
#: ``unsupported_range`` is also what a shallow clone missing the parent reports.
_TRANSIENT_STATUSES = frozenset({"timeout", "unsupported_range"})

#: Marker outcomes beyond the unstored delta statuses. ``deferred`` is the one
#: that is not final: the commit waits for a scan with a full budget. A
#: ``too_large`` marker is not tied to ``REPOWISE_COMMIT_HEALTH_MAX_FILES``:
#: raising that cap rescans marked commits only after an analyzer change.
TOO_LARGE = "too_large"
DEFERRED = "deferred"

#: Seconds one analysed file is assumed to cost when a strict budget decides
#: whether a commit still fits. Measured on pydantic: 0.2-1.9 s a file, under
#: 1 s for most commits. An estimate, so a commit of a few very large files
#: can still overrun; a deadline checked inside the analyzer would close that.
SECONDS_PER_FILE = 1.0


#: A commit touching more files than this is not analysed. The budget below is
#: only checked *between* commits, so a single bulk commit (an initial import
#: of tens of thousands of files) would otherwise run for many minutes and
#: ignore it. The default is the comparison's own ceiling. It counts every
#: changed file, while the comparison counts only eligible code files, so a
#: commit of many non-code files is skipped here where it used to be analysed;
#: that is intended, since reading its change shape is the cost being avoided.
#: ``0`` removes the cap.
def max_files_per_commit() -> int:
    raw = os.environ.get("REPOWISE_COMMIT_HEALTH_MAX_FILES")
    if raw is None:
        return MAX_CHANGED_FILES
    try:
        value = int(raw)
    except ValueError:
        return MAX_CHANGED_FILES
    return value if value >= 0 else MAX_CHANGED_FILES


def changed_file_count(repo_path: str, sha: str) -> int | None:
    """Files *sha* changes against its first parent (all of them for a root commit).

    ``None`` when git cannot answer, so the caller analyses the commit as it
    always did rather than skipping one it merely failed to measure.
    """
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                repo_path,
                "diff-tree",
                "--root",
                "-r",
                "-m",
                "--first-parent",
                "--name-only",
                "--no-commit-id",
                "-z",
                sha,
            ],
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return len({name for name in out.split(b"\0") if name})


@dataclass(slots=True)
class CommitHealthScan:
    """Rows for the commits that were scanned, and how far the scan got."""

    delta_rows: list[dict] = field(default_factory=list)
    finding_rows: list[dict] = field(default_factory=list)
    #: ``git_commit_health_scans`` rows: commits reached that store no delta.
    mark_rows: list[dict] = field(default_factory=list)
    scanned: int = 0
    skipped: int = 0
    #: Commits a strict budget left for later because they would not fit.
    deferred: int = 0
    exhausted_budget: bool = False


def _delta_row(sha: str, delta: ChangeHealthDelta, kept: int) -> dict:
    fp = delta.fingerprint
    return {
        "sha": sha,
        "status": delta.status,
        "analyzer_version": fp.analyzer_version if fp else 0,
        "rules_fingerprint": fp.rules_fingerprint if fp else "",
        "performance_model_version": fp.performance_model_version if fp else 0,
        "introduced_count": delta.introduced_total,
        "worsened_count": delta.worsened_total,
        "resolved_count": delta.resolved_total,
        "improved_count": delta.improved_total,
        "moved_count": delta.moved_total,
        "files_analyzed": delta.scope.analyzed,
        "files_skipped": delta.scope.skipped,
        "findings_stored": kept,
    }


def _ranked(findings: list[ChangeFinding]) -> list[ChangeFinding]:
    """Worst first, so a capped commit keeps the findings worth showing."""
    from .models import SEVERITY_RANK

    return sorted(
        findings,
        key=lambda f: (
            -SEVERITY_RANK.get(f.severity, 0),
            -abs(f.health_impact or 0.0),
            f.path,
            f.change_finding_id,
        ),
    )


def _finding_rows(sha: str, findings: list[ChangeFinding]) -> list[dict]:
    return [
        {
            "sha": sha,
            "position": position,
            "change_finding_id": f.change_finding_id,
            "change_kind": f.change_kind,
            "dimension": f.dimension,
            "biomarker_type": f.biomarker_type,
            "severity": f.severity,
            "severity_before": f.severity_before,
            "file_path": f.path,
            "symbol": f.symbol,
            "line_start": f.line_start,
            "line_end": f.line_end,
            "attribution_basis": f.attribution_basis,
            "health_impact": float(f.health_impact or 0.0),
            "reason": f.reason,
            "moved_from_path": (f.moved_from or {}).get("path"),
            "moved_from_symbol": (f.moved_from or {}).get("symbol"),
        }
        for position, f in enumerate(findings)
    ]


def _mark_row(sha: str, outcome: str, fingerprint: dict[str, Any]) -> dict:
    """A marker, pinned to the versions :func:`current_fingerprint` reads it back under."""
    return {"sha": sha, "outcome": outcome, **fingerprint}


class _Clock:
    """The wall clock one scan may spend; ``total`` 0 means unbounded."""

    def __init__(self, total: float) -> None:
        self.total = total
        self.started = time.monotonic()

    def left(self) -> float:
        return self.total - (time.monotonic() - self.started)

    def spent(self) -> bool:
        return bool(self.total) and self.left() <= 0


def _over_file_cap(repo_path: str, sha: str, file_cap: int) -> bool:
    if not file_cap:
        return False
    count = changed_file_count(repo_path, sha)
    return count is not None and count > file_cap


def _fit(
    service: ChangeHealthDeltaService, repo_path: str, sha: str, clock: _Clock
) -> tuple[RevisionPair | None, str | None]:
    """Whether *sha* fits the time left: ``(pair, None)`` to compare it now.

    Otherwise ``(None, DEFERRED)`` for a commit no inline budget fits, which
    a full-budget scan takes, or ``(None, "")`` for one a later run may fit.
    A commit git cannot resolve here is compared as usual, which reports why.
    """
    try:
        pair = service.source.resolve(sha)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None, None
    cost = service.eligible_count(DeltaRequest(repo_path, sha), pair) * SECONDS_PER_FILE
    if cost <= clock.left():
        return pair, None
    return None, DEFERRED if cost > clock.total else ""


def _compare_into(
    scan: CommitHealthScan,
    service: ChangeHealthDeltaService,
    sha: str,
    pair: RevisionPair | None,
    *,
    repo_path: str,
    fingerprint: dict[str, Any],
    max_findings: int,
) -> None:
    request = DeltaRequest(repo_path, sha)
    try:
        delta = service.compare(request) if pair is None else service.compare(request, pair=pair)
    except Exception:
        # No marker: a git timeout or a lock is worth another try.
        scan.skipped += 1
        return
    scan.scanned += 1
    if delta.status not in _STORED_STATUSES:
        scan.skipped += 1
        if delta.status not in _TRANSIENT_STATUSES:
            scan.mark_rows.append(_mark_row(sha, delta.status, fingerprint))
        return
    kept = _ranked(delta.findings)[:max_findings]
    scan.delta_rows.append(_delta_row(sha, delta, len(kept)))
    scan.finding_rows.extend(_finding_rows(sha, kept))


def scan_commits(
    repo_path: str,
    shas: list[str],
    *,
    service: ChangeHealthDeltaService | None = None,
    budget_seconds: float = 0.0,
    max_findings: int = MAX_FINDINGS_PER_COMMIT,
    strict: bool = False,
) -> CommitHealthScan:
    """Compare each sha against its first parent and build the storable rows.

    *shas* is scanned in order, so callers put the commits they most want
    covered first. A non-zero *budget_seconds* stops the scan once it is spent,
    leaving the rest unscanned; the caller can tell from
    :attr:`CommitHealthScan.exhausted_budget`. The budget is checked between
    commits, so one large commit can overrun it. With *strict*, a commit whose
    estimated cost exceeds the time left is passed over for a smaller one and
    counted in :attr:`CommitHealthScan.deferred`.

    Every commit reached that stores no row gets a marker in
    :attr:`CommitHealthScan.mark_rows`. A commit's rows do not depend on
    *strict* or the budget, only on whether it was reached.
    """
    scan = CommitHealthScan()
    if not shas:
        return scan

    service = service or ChangeHealthDeltaService(
        repo_path=repo_path, rules_fingerprint=health_rules_fingerprint(repo_path)
    )
    clock = _Clock(budget_seconds)
    file_cap = max_files_per_commit()
    fingerprint = current_fingerprint(repo_path)
    for sha in shas:
        if clock.spent():
            scan.exhausted_budget = True
            break
        if _over_file_cap(repo_path, sha, file_cap):
            scan.skipped += 1
            scan.mark_rows.append(_mark_row(sha, TOO_LARGE, fingerprint))
            continue
        pair, wait = (
            _fit(service, repo_path, sha, clock) if strict and clock.total else (None, None)
        )
        if wait is not None:
            scan.deferred += 1
            scan.exhausted_budget = True
            if wait:
                scan.mark_rows.append(_mark_row(sha, wait, fingerprint))
            continue
        _compare_into(
            scan,
            service,
            sha,
            pair,
            repo_path=repo_path,
            fingerprint=fingerprint,
            max_findings=max_findings,
        )
    return scan


def current_fingerprint(repo_path: str) -> dict[str, Any]:
    """What a freshly computed row would be pinned to, without comparing."""
    from repowise.core.analysis.health.engine import HEALTH_ANALYZER_VERSION
    from repowise.core.analysis.health.perf.causal import PERFORMANCE_MODEL_VERSION

    return {
        "analyzer_version": HEALTH_ANALYZER_VERSION,
        "rules_fingerprint": health_rules_fingerprint(repo_path),
        "performance_model_version": PERFORMANCE_MODEL_VERSION,
    }
