"""Per-function coverage and the CRAP score, as pure functions.

CRAP (Change Risk Anti-Patterns) is ``CC² × (1 − cov)³ + CC``: cyclomatic
complexity weighted by how little of the function its tests run. At full
coverage it is just ``CC``; untested, a ``CC`` of 5 already scores 30.

Coverage is *measured* when a coverage report's line sets say which of the
function's executable lines ran. Without that, coverage is a *tier*, never a
number: ``direct`` (a test calls the function), ``reached`` (tests reach it or
its file further out) or ``none``. A tier stands in for a coverage fraction
only inside :func:`function_crap`, to rank estimates against each other, and
never leaves this module as a percentage.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

#: The CRAP at which a function is worth a finding: an untested ``CC`` of 5.
CRAP_THRESHOLD: float = 30.0

CoverageBasis = Literal["measured", "estimated"]
CoverageTier = Literal["direct", "reached", "none"]

# Stand-ins for a tier, for ranking estimates only. A direct test runs most of
# what it calls; reach further out runs some of it. Ceiling: fixed guesses, not
# fitted; a corpus with per-function coverage could fit them.
_TIER_FRACTION: dict[CoverageTier, float] = {"direct": 0.85, "reached": 0.4, "none": 0.0}


@dataclass(frozen=True, slots=True)
class FunctionCrap:
    """One function's CRAP and what its coverage rests on.

    ``coverage_pct`` is set only when measured, ``tier`` only when estimated.
    """

    crap: float
    basis: CoverageBasis
    tier: CoverageTier | None = None
    coverage_pct: float | None = None


def crap_score(ccn: int, coverage_fraction: float) -> float:
    """``CC² × (1 − cov)³ + CC``, with *coverage_fraction* clamped to [0, 1]."""
    uncovered = 1.0 - min(max(coverage_fraction, 0.0), 1.0)
    return ccn * ccn * uncovered**3 + ccn


def function_coverage(
    spans: Sequence[tuple[int, int]],
    covered_lines: Collection[int],
    coverable_lines: Collection[int] | None = None,
) -> list[float | None]:
    """Each span's measured coverage fraction, ``None`` where the lines cannot say.

    A line belongs to the innermost span holding it, so a nested function's
    lines count for it and not for the function around it. The fraction is
    covered over executable lines the span owns, so without *coverable_lines*
    nothing is measured: a report that names only hit lines is silent, not
    zero, about a function none of them fall in.
    """
    coverable = sorted(set(coverable_lines or ()))
    if not coverable:
        return [None] * len(spans)
    covered = set(covered_lines)
    owner: dict[int, int] = {}
    # Outer spans first, so an inner span repaints the lines it holds.
    for i in sorted(range(len(spans)), key=lambda i: (spans[i][0], -spans[i][1])):
        start, end = spans[i]
        for line in coverable[bisect_left(coverable, start) : bisect_right(coverable, end)]:
            owner[line] = i
    owned_covered = [0] * len(spans)
    owned_coverable = [0] * len(spans)
    for line, i in owner.items():
        owned_coverable[i] += 1
        owned_covered[i] += line in covered
    return [
        owned_covered[i] / owned_coverable[i] if owned_coverable[i] else None
        for i in range(len(spans))
    ]


def coverage_tier(test_hops: int | None, file_reached: bool) -> CoverageTier:
    """The tier for a function no measurement covers.

    *test_hops* is the call distance to the nearest test that reaches the
    function, ``None`` when none is known; *file_reached* says tests reach or
    pair with its file.
    """
    if test_hops == 1:
        return "direct"
    if test_hops is not None or file_reached:
        return "reached"
    return "none"


def function_crap(ccn: int, measured: float | None, tier: CoverageTier) -> FunctionCrap:
    """The function's CRAP: measured when a fraction is given, else its tier's estimate.

    A function the measurement says nothing about keeps its estimate, so a
    report that misses it never scores it lower than no report would.
    """
    if measured is not None:
        return FunctionCrap(
            crap_score(ccn, measured), "measured", coverage_pct=round(measured * 100.0, 1)
        )
    return FunctionCrap(crap_score(ccn, _TIER_FRACTION[tier]), "estimated", tier=tier)


def ranked_by_crap(findings: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``untested_complexity`` findings as a list ranked by CRAP, worst first.

    Each row keeps its basis and either the measured percentage or the tier,
    never both, so an estimate is never read as a percentage.
    """
    rows = []
    for f in findings:
        details = f.get("details") or {}
        measured = details.get("coverage_basis") == "measured"
        rows.append(
            {
                "id": f.get("id"),
                "file_path": f.get("file_path"),
                "function_name": f.get("function_name"),
                "line_start": f.get("line_start"),
                "severity": f.get("severity"),
                "crap": details.get("crap"),
                "ccn": details.get("judged_ccn", details.get("ccn")),
                "coverage_basis": details.get("coverage_basis"),
                **(
                    {"coverage_pct": details.get("coverage_pct")}
                    if measured
                    else {"coverage_tier": details.get("coverage_tier")}
                ),
                "action": details.get("action"),
            }
        )
    rows.sort(key=lambda r: (-(r["crap"] or 0.0), r["file_path"] or "", r["line_start"] or 0))
    return rows


def measured_crap(biomarker_type: str, details: Mapping[str, Any] | None) -> float | None:
    """The CRAP of an ``untested_complexity`` finding on measured coverage, else ``None``.

    The one coverage reading a base-to-head comparison keeps: an estimate rests
    on call-graph reach and never makes a finding introduced or worsened.
    """
    if biomarker_type != "untested_complexity" or not details:
        return None
    if details.get("coverage_basis") != "measured":
        return None
    crap = details.get("crap")
    return float(crap) if isinstance(crap, int | float) else None
