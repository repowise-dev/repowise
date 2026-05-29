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

Fires whenever the file carries ≥1 prior fix; severity scales with the count
(and escalates on hotspots, where recent defect history compounds high churn):

- 1 fix      -> LOW
- 2 fixes    -> MEDIUM
- 3-4 fixes  -> HIGH   (CRITICAL if also a churn hotspot)
- 5+ fixes   -> CRITICAL

When git indexing was skipped the field is absent/zero and the detector is
silent.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_WINDOW_DAYS = 180


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
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
                    "window_days": _WINDOW_DAYS,
                },
                reason=(
                    f"{count} bug-{fixes} touched this file in the last "
                    f"~6 months; recent defect history is the strongest "
                    f"cost-effective predictor of further defects"
                ),
            )
        ]


BIOMARKER = PriorDefectDetector()
