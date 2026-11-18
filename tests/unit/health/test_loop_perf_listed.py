"""Loop performance markers judged mostly cold-path are listed, never scored."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult
from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.scoring import attach_impacts, score_file

_LISTED = (
    "io_in_loop",
    "lock_in_loop",
    "string_concat_in_loop",
    "membership_test_against_list_in_loop",
    "list_insert_zero_in_loop",
    "nested_loop_with_io",
)


def _result(name: str, severity: Severity = Severity.HIGH) -> BiomarkerResult:
    return BiomarkerResult(
        biomarker_type=name,
        severity=severity,
        function_name="f",
        line_start=1,
        line_end=2,
        details={},
    )


@pytest.mark.parametrize("name", _LISTED)
def test_a_cold_path_loop_marker_deducts_nothing(name):
    results = [_result(name, Severity.CRITICAL) for _ in range(3)]
    scores, deductions = score_file(results)
    assert scores == {"defect": 10.0, "maintainability": 10.0, "performance": 10.0}
    assert deductions == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("name", _LISTED)
def test_it_stays_a_performance_finding(name):
    """The performance queue and the call-path closure select by this home."""
    (finding,) = attach_impacts([_result(name)], [0.0])
    assert finding.dimension == "performance"


@pytest.mark.parametrize("name", ["hot_path_sync_io", "blocking_sync_in_async"])
def test_unmeasured_perf_markers_still_score(name):
    scores, _ = score_file([_result(name)])
    assert scores["performance"] < 10.0
    assert scores["defect"] == 10.0
