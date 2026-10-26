"""What fixing findings is worth: the file rescored without them.

A finding's ``health_impact`` is its share of the file's deduction, not what
removing it recovers. The saturating category curve, the history cap and its
de-stacking, and the 1.0 floor all sit between the two: on a saturated
category one marker frees little, on a floored file nothing moves, and code
shape removed also lowers what history may take. So every expected gain is
the file scored again through :func:`.scoring.score_file`, never a sum of
impacts.

A removal is ``(finding, share)``: share 1.0 fixes the finding, a smaller one
shrinks its deduction before the curve (an extraction that takes out part of
a method's complexity).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from .biomarkers.base import BiomarkerResult
from .governance import GOVERNANCE_BIOMARKERS
from .history_refresh import as_biomarker_result
from .models import Severity
from .rows import field
from .scoring import HISTORY_CATEGORY, biomarker_category, score_file, severity_deduction
from .shape_curve import continuous_base

Removal = Iterable[tuple[Any, float]]


def removal_gain(findings: Iterable[Any], removed: Removal) -> float:
    """Defect score the file gains when each removed finding loses its share.

    Scores the file twice per call, so pricing every candidate is quadratic in
    the file's findings; cheap at real counts (tens). Cache the "before" side
    per file if a caller ever prices hundreds.
    """
    before, after = _lifted(findings, removed)
    gain = score_file(after)[0]["defect"] - score_file(before)[0]["defect"]
    return round(max(0.0, gain), 3)


def structure_freed(findings: Iterable[Any], removed: Removal) -> float:
    """Code-shape deduction the removal frees, for a caller that models the
    history half itself."""
    before, after = _lifted(findings, removed)
    return round(max(0.0, _structure(before) - _structure(after)), 3)


def _structure(results: list[BiomarkerResult]) -> float:
    _, deductions = score_file(results)
    return sum(
        d
        for r, d in zip(results, deductions, strict=True)
        if biomarker_category(r.biomarker_type) != HISTORY_CATEGORY
    )


def _lifted(
    findings: Iterable[Any], removed: Removal
) -> tuple[list[BiomarkerResult], list[BiomarkerResult]]:
    """The file's scored findings as the scorer takes them, before and after."""
    shares = {id(f): min(max(share, 0.0), 1.0) for f, share in removed}
    before: list[BiomarkerResult] = []
    after: list[BiomarkerResult] = []
    for f in findings:
        # Governance is written by a later pass that deducts nothing
        # (``history_refresh``); a stored ``resolved`` row is no longer detected.
        if (
            field(f, "biomarker_type", "") in GOVERNANCE_BIOMARKERS
            or field(f, "status", None) == "resolved"
        ):
            continue
        result = as_biomarker_result(f)
        before.append(result)
        share = shares.get(id(f), 0.0)
        if share < 1.0:
            after.append(_shrunk(result, 1.0 - share) if share > 0 else result)
    return before, after


def _shrunk(result: BiomarkerResult, keep: float) -> BiomarkerResult:
    """*result* with *keep* of its base deduction, critical scaling applied first."""
    base = result.deduction
    if base is None:
        base = continuous_base(
            result.biomarker_type,
            severity_deduction(result.severity),
            result.severity == Severity.CRITICAL,
            result.details,
        )
    return replace(result, deduction=base * keep)
