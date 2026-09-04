"""Folds over already-loaded health metrics and findings.

Every function here is pure: it takes rows a caller has already read and gives
back plain data. Rows are read through :func:`..rows.field` / :func:`..rows.detail_map`,
so a mapping, an analyzer dataclass and an ORM row all fold identically.

This module owns the arithmetic that REST and the MCP tool used to hold one copy
of each. Response composition, pagination and wire naming stay with the callers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .models import Severity
from .ranking import worst_metric
from .rows import detail_map, field
from .scoring import (
    CATEGORY_CAPS,
    SCORE_FLOOR,
    SCORE_MAX,
    biomarker_category,
    biomarker_weight,
    severity_deduction,
)

__all__ = [
    "MODULE_ROOT_LABEL",
    "SEVERITY_ORDER",
    "biomarker_breakdown",
    "finding_base_deduction",
    "module_label",
    "module_rollups",
    "score_breakdown",
    "severity_breakdown",
]

#: Label for a file that sits directly in the repository root.
MODULE_ROOT_LABEL = "root"

#: The buckets a severity breakdown always declares, worst first. Fixed rather
#: than derived from the data so an absent severity reads as 0, not as missing.
SEVERITY_ORDER: tuple[str, ...] = ("critical", "high", "medium", "low")


def module_label(file_path: str | None) -> str:
    """The top-level directory of *file_path*, or :data:`MODULE_ROOT_LABEL`.

    Only the first segment: ``a/b/c.py`` is ``a`` at any depth. A path with no
    separator has no directory to name, so it rolls up under the root label
    rather than under itself — otherwise every root-level file would be its own
    single-file module.

    Distinct from ``HealthFileMetric.module``, which is the resolved package
    boundary. The two answer different questions and are not interchangeable.
    """
    head, separator, _ = (file_path or "").partition("/")
    return head if separator else MODULE_ROOT_LABEL


def _nloc(row: Any) -> int:
    """A row's NLOC as a non-negative int; a missing or null value is 0."""
    try:
        return max(int(field(row, "nloc", 0) or 0), 0)
    except (TypeError, ValueError):
        return 0


def _score(row: Any) -> float:
    """A row's score; an unmeasured one reads as perfect, not as the worst.

    Tested for ``None`` rather than falsiness: a real ``0.0`` is a score, and
    ``or`` would turn the worst possible file into the best. Unreachable while
    the floor clamps at 1.0, and wrong the moment a legacy row does not.
    """
    score = field(row, "score", SCORE_MAX)
    return float(SCORE_MAX if score is None else score)


def _severity_key(value: Any) -> Any:
    """A severity in the form the scoring tables are keyed on.

    ``Severity`` is a ``StrEnum``, so a member and its lowercase value are the
    same key. A legacy row carrying an unknown or differently-cased string is
    normalized rather than rejected: the lookup tables already default, and a
    malformed stored severity must not turn a read into an error.
    """
    if isinstance(value, Severity):
        return value
    return str(value or "").lower()


def module_rollups(
    metrics: Iterable[Any],
    deductions: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """NLOC-weighted rollups keyed on each row's ``module``.

    Rows without a module are dropped — an unresolved package boundary is not a
    module named ``None``. Worst modules first; ties break on the module name so
    a page boundary is stable across requests.

    *deductions* feeds each module's worst performer the same key the repo-wide
    ranking uses. Passing it is what makes that choice deduction-aware in its own
    right: without it the pick still comes out of the score, but a module whose
    files are all held at the floor resolves on the path instead of on depth.
    """
    buckets: dict[str, list[Any]] = {}
    for row in metrics:
        module = field(row, "module", None)
        if module:
            buckets.setdefault(str(module), []).append(row)

    rows: list[dict[str, Any]] = []
    for name, group in buckets.items():
        # Weight floors at 1 per file, so a non-empty group always has weight
        # and the average never divides by zero.
        total_weight = sum(max(_nloc(row), 1) for row in group)
        average = sum(_score(row) * max(_nloc(row), 1) for row in group) / total_weight
        worst = worst_metric(group, deductions or {})
        rows.append(
            {
                "module": name,
                "file_count": len(group),
                "nloc": sum(_nloc(row) for row in group),
                "average_health": round(average, 2),
                "worst_performer_path": field(worst, "file_path", None),
                "worst_performer_score": round(_score(worst), 2),
            }
        )
    rows.sort(key=lambda row: (row["average_health"], row["module"]))
    return rows


def severity_breakdown(findings: Iterable[Any]) -> dict[str, int]:
    """Counts per declared severity. Unrecognized severities are not counted.

    A ``Severity`` member and its lowercase string are the same dict key, and
    ``dict`` keeps the key it was built with — so the returned keys stay plain
    strings even when every finding carried an enum. Do not "fix" that by
    coercing: the wire shape depends on it.
    """
    out = dict.fromkeys(SEVERITY_ORDER, 0)
    for finding in findings:
        severity = _severity_key(field(finding, "severity", None))
        if severity in out:
            out[severity] += 1
    return out


def biomarker_breakdown(findings: Iterable[Any]) -> list[dict[str, Any]]:
    """Per-biomarker counts split by severity, busiest first.

    ``total`` counts every finding of that biomarker, including one whose
    severity matched no bucket, so it is the honest count and can exceed the
    sum of the four columns. Ties break on the biomarker name.
    """
    by_type: dict[str, dict[str, int]] = {}
    for finding in findings:
        name = field(finding, "biomarker_type", None)
        severity = _severity_key(field(finding, "severity", None))
        bucket = by_type.setdefault(name, {**dict.fromkeys(SEVERITY_ORDER, 0), "total": 0})
        if severity in bucket:
            bucket[severity] += 1
        bucket["total"] += 1
    rows = [{"biomarker_type": name, **counts} for name, counts in by_type.items()]
    rows.sort(key=lambda row: (-row["total"], str(row["biomarker_type"])))
    return rows


def finding_base_deduction(finding: Any) -> float:
    """The pre-cap, pre-weight base deduction for one finding.

    Mirrors ``scoring.score_file``: a continuous ``deduction`` recorded in the
    finding's details (the coverage gradient scales with the uncovered fraction)
    takes the place of the discrete severity table. Reading the override here is
    what lets a breakdown show the continuous signal rather than a band proxy.
    """
    override = detail_map(finding).get("deduction")
    if isinstance(override, (int, float)):
        return float(override)
    return severity_deduction(_severity_key(field(finding, "severity", None)))


def score_breakdown(findings: Sequence[Any]) -> dict[str, Any]:
    """Reconstruct one file's per-category deductions from its open findings.

    The applied impact is the **stored** ``health_impact`` — the exact
    already-weighted-and-capped value the scorer produced at index time — so the
    breakdown reproduces the file's score instead of re-deriving it. Only the
    raw (pre-cap) figure is reconstructed, with the same ``base x weight``
    formula, so a capped category is honest about how much it shed.

    Categories are emitted in :data:`CATEGORY_CAPS` order and a category with no
    findings is omitted, not zero-filled.
    """
    per_category: dict[str, list[Any]] = {}
    for finding in findings:
        category = biomarker_category(field(finding, "biomarker_type", None))
        per_category.setdefault(category, []).append(finding)

    categories: list[dict[str, Any]] = []
    total_deduction = 0.0
    for category, cap in CATEGORY_CAPS.items():
        entries = per_category.get(category, [])
        if not entries:
            continue
        raw_each = [
            finding_base_deduction(f) * biomarker_weight(field(f, "biomarker_type", None))
            for f in entries
        ]
        applied_each = [float(field(f, "health_impact", 0.0) or 0.0) for f in entries]
        raw_sum = sum(raw_each)
        applied_sum = sum(applied_each)
        categories.append(
            {
                "category": category,
                "cap": round(cap, 2),
                "raw_deduction": round(raw_sum, 3),
                "applied_deduction": round(applied_sum, 3),
                # Category shed weight iff its applied total is held at the cap.
                "capped": applied_sum < raw_sum - 1e-6,
                "finding_count": len(entries),
                "findings": [
                    {
                        "id": field(f, "id", None),
                        "biomarker_type": field(f, "biomarker_type", None),
                        "severity": field(f, "severity", None),
                        "raw_impact": round(raw, 3),
                        "applied_impact": round(applied, 3),
                        "function_name": field(f, "function_name", None),
                        "reason": field(f, "reason", None),
                    }
                    for f, raw, applied in zip(entries, raw_each, applied_each, strict=True)
                ],
            }
        )
        total_deduction += applied_sum
    score = max(SCORE_FLOOR, min(SCORE_MAX, SCORE_MAX - total_deduction))
    return {
        "score": round(score, 2),
        "total_deduction": round(total_deduction, 3),
        "categories": categories,
    }
