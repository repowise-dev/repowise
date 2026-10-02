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

Only clones shared with another file count. A clone inside one file is
mostly a run of sibling members with the same shape (getters, operator
overloads, one-call test actions), which the token-shape matcher pairs with
each other; its "partner" is the file itself, so headlining it as duplicated
misreads structure as copying, and co-change, the signal this marker grades
on, means nothing for a file paired with itself. A real copy inside one file
is lost with them; that is the price of not reporting the rest.
"""

from __future__ import annotations

from ..duplication.detector import ClonePair, _union_line_count
from ..models import Severity
from .base import BiomarkerResult, FileContext

_MIN_DUP_PCT = 8.0
_MIN_CLONE_LINES = 6
_ACTIVE_CO_CHANGE = 3


class DryViolationDetector:
    name = "dry_violation"
    category = "duplication"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        cross = [p for p in ctx.clones if not p.is_intra_file]
        if not cross:
            return []
        dup_pct = _cross_file_pct(ctx.file_path, ctx.clones, cross, ctx.duplication_pct or 0.0)
        if dup_pct < _MIN_DUP_PCT:
            return []

        # Pick the worst clone — biggest active region wins over a
        # slightly larger dormant clone.
        worst = max(
            cross,
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
                line_end=worst.a_end_line if worst.file_a == ctx.file_path else worst.b_end_line,
                details={
                    "duplication_pct": dup_pct,
                    "clone_pair_count": len(cross),
                    "worst_clone_lines": worst_lines,
                    "worst_clone_partner": partner,
                    "worst_clone_co_change": round(worst.co_change_count, 2),
                },
                reason=(
                    f"{dup_pct:.0f}% of file duplicated in other files; worst clone shares "
                    f"{worst_lines} lines with {partner}"
                    + (
                        f" (co-changed {round(worst.co_change_count, 2)}x)"
                        if worst.co_change_count
                        else ""
                    )
                ),
            )
        ]


def _side(path: str, pair: ClonePair) -> tuple[int, int]:
    if pair.file_a == path:
        return pair.a_start_line, pair.a_end_line
    return pair.b_start_line, pair.b_end_line


def _cross_file_pct(
    path: str, clones: list[ClonePair], cross: list[ClonePair], file_pct: float
) -> float:
    """The share of the file duplicated in other files, scaled from *file_pct*.

    *file_pct* (every clone, intra-file ones included) is what the detector
    measured against the file's NLOC, which this marker does not see, so the
    cross-file share is the same percentage scaled by the lines only
    cross-file clones cover. Exact unless *file_pct* was capped at 100, and
    then it can only come out lower.
    """
    every: list[tuple[int, int]] = [_side(path, p) for p in cross]
    covered_cross = _union_line_count(every)
    for p in clones:
        if p.is_intra_file:
            every += [(p.a_start_line, p.a_end_line), (p.b_start_line, p.b_end_line)]
    covered = _union_line_count(every)
    return round(file_pct * covered_cross / covered, 2) if covered else 0.0


BIOMARKER = DryViolationDetector()
