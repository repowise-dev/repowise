"""DRY Violation — significant duplication, weighted by co-change.

Fires when a file contains clone pairs that pass a minimum-size gate.
Severity grades on:

- how much of the file is duplicated (``duplication_pct``)
- the maximum co-change count across the file's clone pairs — actively
  co-modified duplication is a far stronger smell than dormant clones
  that haven't been touched together in months.

The detector reads from ``ctx.clones`` / ``ctx.duplication_pct``, which
the engine populates from ``duplication.detect_clones`` once per
analyze() call.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_MIN_DUP_PCT = 8.0
_MIN_CLONE_LINES = 6
_ACTIVE_CO_CHANGE = 3


class DryViolationDetector:
    name = "dry_violation"
    category = "duplication"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if not ctx.clones:
            return []
        dup_pct = ctx.duplication_pct or 0.0
        if dup_pct < _MIN_DUP_PCT:
            return []

        # Pick the worst clone — biggest active region wins over a
        # slightly larger dormant clone.
        worst = max(
            ctx.clones,
            key=lambda p: (p.co_change_count, max(p.a_line_count, p.b_line_count)),
        )
        worst_lines = max(worst.a_line_count, worst.b_line_count)
        if worst_lines < _MIN_CLONE_LINES:
            return []

        partner = worst.file_b if worst.file_a == ctx.file_path else worst.file_a

        active = worst.co_change_count >= _ACTIVE_CO_CHANGE
        if active and dup_pct >= 25.0:
            severity = Severity.HIGH
        elif active or dup_pct >= 25.0:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=severity,
                function_name=None,
                line_start=worst.a_start_line
                if worst.file_a == ctx.file_path
                else worst.b_start_line,
                line_end=worst.a_end_line
                if worst.file_a == ctx.file_path
                else worst.b_end_line,
                details={
                    "duplication_pct": dup_pct,
                    "clone_pair_count": len(ctx.clones),
                    "worst_clone_lines": worst_lines,
                    "worst_clone_partner": partner,
                    "worst_clone_co_change": worst.co_change_count,
                },
                reason=(
                    f"{dup_pct:.0f}% of file duplicated; worst clone shares "
                    f"{worst_lines} lines with {partner}"
                    + (
                        f" (co-changed {worst.co_change_count}×)"
                        if worst.co_change_count
                        else ""
                    )
                ),
            )
        ]


BIOMARKER = DryViolationDetector()
