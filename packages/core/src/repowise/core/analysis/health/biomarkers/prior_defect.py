"""Prior Defect — a file with recent bug-fix history.

Defects cluster: the single strongest cheap predictor of "will this file be
bug-fixed soon" is "was it bug-fixed recently" (Ostrand & Weyuker; Kim et al.
"bug cache"). On the defect benchmark the prior-defects baseline is the most
cost-effective signal (highest Popt) — it flags only files that already proved
fragile, so inspecting them per line of code catches defects efficiently.

The git indexer counts, per file, the bug-fix commits touching it in the
trailing ~6-month window (``git_meta["prior_defect_count"]``), classified by the
same keyword rule the benchmark labels fixes with, and anchored to the index's
``as_of`` reference so a historical/T0 checkout measures the window *before* that
commit — no leakage from future fixes. This biomarker is the consumer.

Fires when the file's fix count is in the top fifth *for this repository*
(``prior_defect_pct`` ≥ 0.80). The count itself is not repo-relative: six
months is a fixed window, so a repository landing many commits a day
accumulates more fixes per file inside it than a quiet one, and an entry gate
of one fix then fires on a large share of the tree. Ranking the count decides
the entry; the ladder below, which the benchmark calibrated, decides severity:

- 1 fix      -> LOW
- 2 fixes    -> MEDIUM
- 3-4 fixes  -> HIGH   (CRITICAL if also a churn hotspot)
- 5+ fixes   -> CRITICAL

The rank is taken over every file, ties sharing a rank, because a file with no
fixes in the window was measured and found clean rather than not measured.

When git indexing was skipped the fields are absent/zero and the detector is
silent.
"""

from __future__ import annotations

from ..models import Severity
from ..semantics import format_top_percentile
from .base import BiomarkerResult, FileContext

_WINDOW_DAYS = 180
_MIN_PERCENTILE = 0.80


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class PriorDefectDetector:
    name = "prior_defect"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        meta = ctx.git_meta or {}
        count = _as_int(meta.get("prior_defect_count"))
        if count < 1:
            return []
        percentile = _as_float(meta.get("prior_defect_pct"))
        if percentile < _MIN_PERCENTILE:
            return []

        is_hotspot = bool(meta.get("is_hotspot"))
        if count >= 5:
            severity = Severity.CRITICAL
        elif count >= 3:
            severity = Severity.CRITICAL if is_hotspot else Severity.HIGH
        elif count >= 2:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        fixes = "fix" if count == 1 else "fixes"
        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=severity,
                function_name=None,
                line_start=None,
                line_end=None,
                details={
                    "prior_defect_count": count,
                    # 0-100, as every other emitted percentile.
                    "prior_defect_pct": round(percentile * 100.0, 1),
                    "window_days": _WINDOW_DAYS,
                },
                reason=(
                    f"{count} bug-{fixes} touched this file in the last ~6 months "
                    f"({format_top_percentile(percentile, 'files in this repository')}); "
                    f"recent defect history is the strongest cost-effective "
                    f"predictor of further defects"
                ),
            )
        ]


BIOMARKER = PriorDefectDetector()
