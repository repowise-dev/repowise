"""Does the score actually find the buggy files?

A small, interpretable self-validation of the health score, computed purely
from the metrics + findings the analyzer already produced — no git pass, no
extra artifact, no re-index.

Label: a file is "recently bug-fixed" when it carries a ``prior_defect``
biomarker (the indexer attributes Conventional-Commit ``fix:`` commits in a
~180-day window to the files they touched). We then ask: of the K
lowest-health files, how many were recently bug-fixed, vs. the repo-wide base
rate. ``prior_defect`` is one (down-weighted) input to the score, so this is an
association on the indexed history, not a leakage-free forward prediction — the
UI/CLI disclose that.

The two inputs are duck-typed: each entry may be a mapping (``m["score"]``) or
an object with attributes (``m.score``), so the same function serves the server
(dict rows), the CLI (``HealthFileMetricData`` / ``HealthFindingData``), and the
hosted backend (``health.json`` dicts) without per-call-site adapters.
"""

from __future__ import annotations

from typing import Any

_DEFAULT_K = 20
_MIN_FILES = 25          # below this a precision@K headline is noise
_MIN_DEFECT_FILES = 5    # need a real positive class to divide by
_DEFAULT_WINDOW_DAYS = 180


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a mapping or an attribute-bearing object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _recent_fix_counts(findings: list[Any]) -> tuple[dict[str, int], int]:
    """Map file_path -> recent bug-fix count, plus the window in days."""
    counts: dict[str, int] = {}
    window_days = _DEFAULT_WINDOW_DAYS
    for f in findings:
        if _get(f, "biomarker_type") != "prior_defect":
            continue
        details = _get(f, "details") or {}
        path = _get(f, "file_path", "")
        counts[path] = int(details.get("prior_defect_count", 1) or 1)
        window_days = int(details.get("window_days", window_days) or window_days)
    counts.pop("", None)
    return counts, window_days


def compute_defect_accuracy(
    metrics: list[Any],
    findings: list[Any],
    *,
    k: int = _DEFAULT_K,
) -> dict[str, Any] | None:
    """Precision-style accuracy summary, or ``None`` when there isn't enough
    signal to show an honest number (too few files / too few defects)."""
    scored = [m for m in metrics if _get(m, "file_path")]
    n = len(scored)
    fix_counts, window_days = _recent_fix_counts(findings)
    total_defect_files = sum(
        1 for m in scored if fix_counts.get(_get(m, "file_path"), 0) > 0
    )

    if n < _MIN_FILES or total_defect_files < _MIN_DEFECT_FILES:
        return None

    ranked = sorted(scored, key=lambda m: float(_get(m, "score", 10.0)))

    def hits_in(top: list[Any]) -> int:
        return sum(1 for m in top if fix_counts.get(_get(m, "file_path"), 0) > 0)

    kk = min(k, n)
    hits = hits_in(ranked[:kk])
    base_rate = total_defect_files / n
    precision = hits / kk

    # Concentration: share of all recently-fixed files that fall in the
    # least-healthy 20% — the "something to think about" Pareto stat.
    k_conc = max(1, round(n * 0.20))
    conc_share = hits_in(ranked[:k_conc]) / total_defect_files

    # Per-K table for the dig-deeper breakdown.
    table = [
        {"k": kx, "hits": hits_in(ranked[:kx])}
        for kx in (10, 20, 30)
        if kx <= n
    ]

    # The flagged files themselves, for a bulletproof drill-down.
    sample = [
        {
            "file_path": _get(m, "file_path"),
            "score": round(float(_get(m, "score", 10.0)), 2),
            "recent_fixes": fix_counts.get(_get(m, "file_path"), 0),
        }
        for m in ranked[:kk]
    ]

    return {
        "k": kk,
        "hits": hits,
        "precision": round(precision, 4),
        "base_rate": round(base_rate, 4),
        "lift": round(precision / base_rate, 2) if base_rate else None,
        "window_days": window_days,
        "scored_files": n,
        "defect_files": total_defect_files,
        "concentration_file_fraction": 0.20,
        "concentration_defect_share": round(conc_share, 4),
        "precision_table": table,
        "flagged_files": sample,
    }
