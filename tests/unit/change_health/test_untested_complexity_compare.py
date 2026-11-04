"""``untested_complexity`` in a base-to-head comparison.

Only CRAP on measured coverage takes part: a function's measured CRAP rising
worsens it and a new one crossing the bar introduces it. An estimate rests on
call-graph reach and never makes a finding introduced or worsened.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.change_health.analyzer import RevisionHealthAnalyzer
from repowise.core.analysis.change_health.index_inputs import IndexInputs
from repowise.core.analysis.change_health.matcher import _classify
from repowise.core.analysis.change_health.models import ChangeFinding
from repowise.core.analysis.change_health.service import _comparable
from repowise.core.analysis.health.coverage.model import coverage_map_entry, file_coverage
from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health_gate import NOTE, WARNING, _level

from .conftest import python_complex

_PATH = "src/hot.py"


def _finding(basis: str, crap: float, severity: Severity = Severity.HIGH) -> HealthFindingData:
    cov = {"coverage_pct": 10.0} if basis == "measured" else {"coverage_tier": "none"}
    return HealthFindingData(
        biomarker_type="untested_complexity",
        severity=severity,
        file_path=_PATH,
        function_name="run",
        line_start=1,
        line_end=30,
        details={"crap": crap, "ccn": 8, "coverage_basis": basis, **cov},
        reason="run has CRAP",
        health_impact=0.0,
        dimension="advisory",
    )


def test_only_measured_crap_is_compared() -> None:
    assert _comparable(_finding("measured", 56.0), frozenset())
    assert not _comparable(_finding("estimated", 72.0), frozenset())


@pytest.mark.parametrize(
    ("before", "after", "kind"),
    [(56.0, 80.0, "worsened"), (80.0, 56.0, "improved"), (56.0, 56.2, "unchanged")],
)
def test_measured_crap_moving_within_a_band(before: float, after: float, kind: str) -> None:
    assert _classify(_finding("measured", before), _finding("measured", after)) == kind


def _run(index: IndexInputs | None):
    return RevisionHealthAnalyzer().analyze(
        {_PATH: python_complex("run", 8).encode()}, subject_paths={_PATH}, index=index
    )


def _crap(run) -> list[HealthFindingData]:
    return [f for f in run.findings if f.biomarker_type == "untested_complexity"]


def test_without_a_report_nothing_is_comparable() -> None:
    """A comparison reads no stored coverage, so every finding is an estimate."""
    found = _crap(_run(None))
    assert found and all(f.details["coverage_basis"] == "estimated" for f in found)
    assert not any(_comparable(f, frozenset()) for f in found)


def test_the_same_report_on_both_sides_changes_nothing() -> None:
    """Base and head read one stored report, so unchanged code stays unchanged."""
    lines = range(1, 200)
    entry = coverage_map_entry(file_coverage(_PATH, (), lines), "lcov")
    index = IndexInputs(coverage={_PATH: entry})
    base, head = _crap(_run(index)), _crap(_run(index))
    assert base and all(f.details["coverage_basis"] == "measured" for f in head)
    assert [_classify(b, h) for b, h in zip(base, head, strict=True)] == ["unchanged"] * len(head)


def _change(**kw) -> ChangeFinding:
    base = dict(
        change_finding_id="chf_1",
        change_kind="introduced",
        dimension="advisory",
        biomarker_type="untested_complexity",
        severity="critical",
        path=_PATH,
        symbol="run",
        line_start=1,
        line_end=30,
        reason="run has CRAP 110",
        attribution_basis="added_lines",
        attribution_confidence="high",
        attribution_detail="",
        suggestion="",
        follow_up="",
        finding_id="finding_abc",
    )
    base.update(kw)
    return ChangeFinding(**base)


def test_a_measured_regression_warns_and_never_fails() -> None:
    assert _level(_change(), "introduced", "medium") == WARNING
    assert _level(_change(change_kind="worsened"), "worsened", "medium") == WARNING
    assert _level(_change(attribution_basis="moved"), "worsened", "medium") == NOTE


def test_sarif_carries_the_rule_with_the_line_free_finding_id() -> None:
    from repowise.core.analysis.health_gate import evaluate, render_sarif
    from tests.unit.analysis.test_health_gate import delta

    result = evaluate(delta(_change()))
    assert result.verdict == "warn"
    run = render_sarif(result, tool_version="0")["runs"][0]
    assert [r["id"] for r in run["tool"]["driver"]["rules"]] == ["untested_complexity"]
    assert run["results"][0]["partialFingerprints"] == {"repowiseHealth/v1": "finding_abc"}
