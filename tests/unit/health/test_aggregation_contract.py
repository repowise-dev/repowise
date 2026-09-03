"""Module rollups, breakdowns and category caps, over every record shape.

These folds were held in two copies each — one in the REST router package, one
in the MCP tool — over rows that arrive as ORM objects, analyzer dataclasses or
plain dicts depending on the caller. The copies are gone; this pins what the
one implementation must do, including the cases the copies never agreed on:
floor ties, malformed legacy severity, absent categories, and stored applied
impact taking precedence over anything recomputed.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field

import pytest

from repowise.core.analysis.health.aggregation import (
    MODULE_ROOT_LABEL,
    biomarker_breakdown,
    finding_base_deduction,
    module_label,
    module_rollups,
    score_breakdown,
    severity_breakdown,
)
from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.scoring import (
    CATEGORY_CAPS,
    SCORE_FLOOR,
    SCORE_MAX,
    biomarker_category,
)


@dataclass
class _Finding:
    biomarker_type: str
    severity: str = "high"
    health_impact: float = 0.0
    details: dict = dc_field(default_factory=dict)
    id: str = "f1"
    function_name: str | None = None
    reason: str = ""


class _MetricRow:
    def __init__(self, file_path: str, score: float, nloc: int, module: str | None) -> None:
        self.file_path = file_path
        self.score = score
        self.nloc = nloc
        self.module = module


def _metric_dict(file_path: str, score: float, nloc: int, module: str | None) -> dict:
    return {"file_path": file_path, "score": score, "nloc": nloc, "module": module}


# --------------------------------------------------------------------------- #
# Module label policy
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path,expected",
    [
        ("packages/core/src/a.py", "packages"),
        ("packages/a.py", "packages"),
        ("README.md", MODULE_ROOT_LABEL),
        ("", MODULE_ROOT_LABEL),
        (None, MODULE_ROOT_LABEL),
        ("src/", "src"),
    ],
)
def test_module_label_takes_the_first_segment_or_the_root_label(path, expected) -> None:
    assert module_label(path) == expected


# --------------------------------------------------------------------------- #
# Module rollups
# --------------------------------------------------------------------------- #


def test_module_rollups_are_identical_for_dicts_and_attribute_rows() -> None:
    rows = [
        ("api/a.py", 4.0, 100, "api"),
        ("api/b.py", 8.0, 300, "api"),
        ("web/c.py", 6.0, 50, "web"),
    ]
    as_dicts = module_rollups([_metric_dict(*r) for r in rows])
    as_objects = module_rollups([_MetricRow(*r) for r in rows])
    assert as_dicts == as_objects
    assert [r["module"] for r in as_dicts] == ["web", "api"]  # worst average first
    api = as_dicts[1]
    assert api["file_count"] == 2
    assert api["nloc"] == 400
    # NLOC-weighted: (4*100 + 8*300) / 400.
    assert api["average_health"] == 7.0
    assert api["worst_performer_path"] == "api/a.py"
    assert api["worst_performer_score"] == 4.0


def test_rows_without_a_module_are_dropped_not_bucketed_under_none() -> None:
    rollups = module_rollups(
        [_metric_dict("a.py", 3.0, 10, None), _metric_dict("api/b.py", 9.0, 10, "api")]
    )
    assert [r["module"] for r in rollups] == ["api"]


def test_module_rollup_order_is_total_across_equal_averages() -> None:
    rows = [
        _metric_dict("b/x.py", 5.0, 10, "b"),
        _metric_dict("a/x.py", 5.0, 10, "a"),
        _metric_dict("c/x.py", 1.0, 10, "c"),
    ]
    assert [r["module"] for r in module_rollups(rows)] == ["c", "a", "b"]


def test_a_module_worst_performer_uses_depth_when_the_scores_tie() -> None:
    rows = [
        _metric_dict("api/z.py", SCORE_FLOOR, 10, "api"),
        _metric_dict("api/a.py", SCORE_FLOOR, 10, "api"),
    ]
    deepest = module_rollups(rows, {"api/z.py": 12.0, "api/a.py": 3.0})
    assert deepest[0]["worst_performer_path"] == "api/z.py"


def test_without_depth_a_tied_module_worst_performer_falls_to_the_path() -> None:
    rows = [
        _metric_dict("api/z.py", SCORE_FLOOR, 10, "api"),
        _metric_dict("api/a.py", SCORE_FLOOR, 10, "api"),
    ]
    assert module_rollups(rows)[0]["worst_performer_path"] == "api/a.py"


def test_a_zero_score_is_a_score_not_a_missing_measurement() -> None:
    """``or SCORE_MAX`` here would rank the worst possible file as the best."""
    rows = [
        _metric_dict("api/a.py", 0.0, 10, "api"),
        _metric_dict("api/b.py", 10.0, 10, "api"),
    ]
    rollup = module_rollups(rows)[0]
    assert rollup["average_health"] == 5.0
    assert rollup["worst_performer_score"] == 0.0
    assert rollup["worst_performer_path"] == "api/a.py"


def test_a_missing_nloc_weighs_one_rather_than_dividing_by_zero() -> None:
    rows = [
        {"file_path": "api/a.py", "score": 2.0, "module": "api"},
        {"file_path": "api/b.py", "score": 8.0, "module": "api"},
    ]
    rollup = module_rollups(rows)[0]
    assert rollup["nloc"] == 0
    assert rollup["average_health"] == 5.0


# --------------------------------------------------------------------------- #
# Severity + biomarker breakdowns
# --------------------------------------------------------------------------- #


def test_severity_breakdown_always_declares_every_bucket() -> None:
    assert severity_breakdown([]) == {"critical": 0, "high": 0, "medium": 0, "low": 0}


def test_severity_breakdown_reads_enums_strings_and_casing_alike() -> None:
    findings = [
        _Finding("a", severity=Severity.HIGH),
        {"biomarker_type": "b", "severity": "high"},
        _Finding("c", severity="HIGH"),
    ]
    assert severity_breakdown(findings)["high"] == 3


def test_malformed_legacy_severity_is_dropped_not_raised() -> None:
    findings = [_Finding("a", severity="informational"), _Finding("b", severity=None)]
    assert severity_breakdown(findings) == {"critical": 0, "high": 0, "medium": 0, "low": 0}


def test_biomarker_totals_count_a_malformed_severity_the_buckets_cannot() -> None:
    rows = biomarker_breakdown([_Finding("god_class", severity="informational")])
    assert rows == [
        {
            "biomarker_type": "god_class",
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "total": 1,
        }
    ]


def test_biomarker_breakdown_is_busiest_first_with_a_total_order() -> None:
    findings = [
        _Finding("zeta"),
        _Finding("alpha"),
        _Finding("busy"),
        _Finding("busy"),
    ]
    assert [r["biomarker_type"] for r in biomarker_breakdown(findings)] == [
        "busy",
        "alpha",
        "zeta",
    ]


# --------------------------------------------------------------------------- #
# Score / category breakdown
# --------------------------------------------------------------------------- #


def _category_biomarker(category: str) -> str:
    """A biomarker name the scorer files under *category*."""
    for name in ("god_class", "complex_method", "coverage_gap", "dry_violation", "error_handling"):
        if biomarker_category(name) == category:
            return name
    raise AssertionError(f"no biomarker maps to {category}")


def test_a_continuous_deduction_override_replaces_the_severity_band() -> None:
    finding = _Finding("coverage_gradient", severity="low", details={"deduction": 3.25})
    assert finding_base_deduction(finding) == 3.25


def test_the_override_is_read_from_stored_details_json_too() -> None:
    stored = {
        "biomarker_type": "coverage_gradient",
        "severity": "low",
        "details_json": '{"deduction": 2.5}',
    }
    assert finding_base_deduction(stored) == 2.5


def test_a_malformed_severity_falls_back_instead_of_raising() -> None:
    assert finding_base_deduction(_Finding("god_class", severity="informational")) == 0.5


def test_the_breakdown_reproduces_the_score_from_stored_impact() -> None:
    biomarker = _category_biomarker("structural_complexity")
    findings = [_Finding(biomarker, health_impact=1.5), _Finding(biomarker, health_impact=0.75)]
    out = score_breakdown(findings)
    assert out["total_deduction"] == 2.25
    assert out["score"] == round(SCORE_MAX - 2.25, 2)
    assert out["categories"][0]["applied_deduction"] == 2.25


def test_a_capped_category_is_flagged_and_holds_its_applied_total() -> None:
    biomarker = _category_biomarker("structural_complexity")
    cap = CATEGORY_CAPS["structural_complexity"]
    findings = [_Finding(biomarker, severity="critical", health_impact=cap / 4) for _ in range(4)]
    category = score_breakdown(findings)["categories"][0]
    assert category["applied_deduction"] == pytest.approx(cap)
    assert category["raw_deduction"] > cap
    assert category["capped"] is True


def test_a_category_with_no_findings_is_absent_rather_than_zero_filled() -> None:
    biomarker = _category_biomarker("structural_complexity")
    out = score_breakdown([_Finding(biomarker, health_impact=1.0)])
    assert [c["category"] for c in out["categories"]] == ["structural_complexity"]


def test_the_breakdown_never_reports_below_the_floor() -> None:
    biomarker = _category_biomarker("structural_complexity")
    findings = [_Finding(biomarker, health_impact=50.0)]
    assert score_breakdown(findings)["score"] == SCORE_FLOOR


def test_the_breakdown_is_identical_for_dataclasses_and_stored_rows() -> None:
    biomarker = _category_biomarker("structural_complexity")
    as_objects = [_Finding(biomarker, severity="high", health_impact=1.5, id="x")]
    as_dicts = [
        {
            "biomarker_type": biomarker,
            "severity": "high",
            "health_impact": 1.5,
            "details_json": "{}",
            "id": "x",
            "function_name": None,
            "reason": "",
        }
    ]
    assert score_breakdown(as_objects) == score_breakdown(as_dicts)


def test_a_null_health_impact_counts_as_no_deduction() -> None:
    biomarker = _category_biomarker("structural_complexity")
    out = score_breakdown([{"biomarker_type": biomarker, "severity": "high", "health_impact": None}])
    assert out["total_deduction"] == 0.0
    assert out["score"] == SCORE_MAX
