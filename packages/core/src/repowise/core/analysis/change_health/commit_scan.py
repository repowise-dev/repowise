"""Run the base-versus-head comparison over a list of commits, for storage.

Turns shas into the rows ``git_commit_health_deltas`` and
``git_commit_health_findings`` hold, so a host with no working tree can answer
for a commit the viewer picks at random.

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

#: Findings kept per commit. The delta row carries the true totals, so a
#: capped commit still reports honestly how much it is not showing.
MAX_FINDINGS_PER_COMMIT = 50

#: Statuses worth a row. A comparison that could not run is not a clean
#: commit, and storing it would make "no findings" ambiguous forever.
_STORED_STATUSES = frozenset({"available", "partial"})

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
    scanned: int = 0
    skipped: int = 0
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


def scan_commits(
    repo_path: str,
    shas: list[str],
    *,
    service: ChangeHealthDeltaService | None = None,
    budget_seconds: float = 0.0,
    max_findings: int = MAX_FINDINGS_PER_COMMIT,
) -> CommitHealthScan:
    """Compare each sha against its first parent and build the storable rows.

    *shas* is scanned in order, so callers put the commits they most want
    covered first. A non-zero *budget_seconds* stops the scan once it is spent,
    leaving the rest unscanned; the caller can tell from
    :attr:`CommitHealthScan.exhausted_budget`.
    """
    scan = CommitHealthScan()
    if not shas:
        return scan

    service = service or ChangeHealthDeltaService(
        repo_path=repo_path, rules_fingerprint=health_rules_fingerprint(repo_path)
    )
    started = time.monotonic()
    file_cap = max_files_per_commit()
    for sha in shas:
        if budget_seconds and time.monotonic() - started >= budget_seconds:
            scan.exhausted_budget = True
            break
        if file_cap:
            count = changed_file_count(repo_path, sha)
            if count is not None and count > file_cap:
                scan.skipped += 1
                continue
        try:
            delta = service.compare(DeltaRequest(repo_path, sha))
        except Exception:
            scan.skipped += 1
            continue
        scan.scanned += 1
        if delta.status not in _STORED_STATUSES:
            scan.skipped += 1
            continue
        kept = _ranked(delta.findings)[:max_findings]
        scan.delta_rows.append(_delta_row(sha, delta, len(kept)))
        scan.finding_rows.extend(_finding_rows(sha, kept))
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
