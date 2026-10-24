"""Per-function coverage, the CRAP score and the ``untested_complexity`` finding."""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.untested_complexity import BIOMARKER
from repowise.core.analysis.health.complexity.models import FunctionComplexity
from repowise.core.analysis.health.crap import (
    CRAP_THRESHOLD,
    coverage_tier,
    crap_score,
    function_coverage,
    function_crap,
)
from repowise.core.ingestion.parser import parse_file
from repowise.core.ingestion.traverser import FileTraverser

# --------------------------------------------------------------------------
# The formula
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ccn", "cov", "expected"),
    [
        (5, 0.0, 30.0),  # the threshold: an untested CC of 5
        (10, 1.0, 10.0),  # full coverage leaves CC
        (10, 0.5, 22.5),
        (1, 0.0, 2.0),
        (30, 1.0, 30.0),  # cannot get under the bar by testing
        (4, -0.5, 20.0),  # clamped to [0, 1]
        (4, 1.5, 4.0),
    ],
)
def test_crap_score(ccn: int, cov: float, expected: float) -> None:
    assert crap_score(ccn, cov) == pytest.approx(expected)


def test_threshold_is_an_untested_cc_of_five() -> None:
    assert crap_score(5, 0.0) == CRAP_THRESHOLD


# --------------------------------------------------------------------------
# Measured per-function coverage
# --------------------------------------------------------------------------


def test_span_edges_are_inclusive() -> None:
    assert function_coverage([(3, 5)], {3, 5}, {2, 3, 4, 5, 6}) == [pytest.approx(2 / 3)]


def test_a_nested_function_owns_its_own_lines() -> None:
    outer, inner = (1, 10), (3, 5)
    got = function_coverage([outer, inner], covered_lines={1, 2, 3}, coverable_lines={1, 2, 3, 4, 6, 7})
    # outer owns 1, 2, 6, 7; inner owns 3, 4
    assert got == [pytest.approx(0.5), pytest.approx(0.5)]


def test_covered_lines_outside_the_executable_set_do_not_count() -> None:
    assert function_coverage([(1, 4)], {1, 2, 3}, {1, 4}) == [pytest.approx(0.5)]


def test_a_span_with_no_executable_line_is_unmeasured() -> None:
    assert function_coverage([(20, 25)], {1}, {1, 2}) == [None]


def test_without_executable_lines_nothing_is_measured() -> None:
    assert function_coverage([(1, 5), (6, 9)], {2}, None) == [None, None]


# --------------------------------------------------------------------------
# Tiers and the estimate
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hops", "reached", "tier"),
    [(1, False, "direct"), (2, False, "reached"), (None, True, "reached"), (None, False, "none")],
)
def test_coverage_tier(hops: int | None, reached: bool, tier: str) -> None:
    assert coverage_tier(hops, reached) == tier


def test_an_estimate_carries_a_tier_and_never_a_percentage() -> None:
    result = function_crap(8, None, "reached")
    assert result.basis == "estimated"
    assert result.tier == "reached"
    assert result.coverage_pct is None


def test_a_measurement_carries_a_percentage_and_no_tier() -> None:
    result = function_crap(8, 0.25, "none")
    assert (result.basis, result.tier, result.coverage_pct) == ("measured", None, 25.0)


@pytest.mark.parametrize(("hops", "reached"), [(None, False), (None, True), (2, False)])
def test_a_function_the_report_misses_keeps_its_estimate(hops, reached) -> None:
    """A report that says nothing about a function never scores it below no report."""
    fn = _fn("f", 10, 30, 12)
    alone = _ctx([fn], reached_by_tests=reached, function_test_hops={10: hops} if hops else {})
    lines = set(range(1, 6))
    reported = _ctx(
        [fn],
        reached_by_tests=reached,
        function_test_hops={10: hops} if hops else {},
        line_coverage_pct=100.0,
        covered_lines=lines,
        coverable_lines=lines,
        total_coverable_lines=5,
    )
    assert _one(reported).details == _one(alone).details
    assert _one(reported).details["coverage_basis"] == "estimated"


# --------------------------------------------------------------------------
# The biomarker
# --------------------------------------------------------------------------


def _fn(name: str, start: int, end: int, ccn: int) -> FunctionComplexity:
    return FunctionComplexity(
        name=name, start_line=start, end_line=end, ccn=ccn, max_nesting=1, cognitive=ccn, nloc=end - start
    )


def _ctx(functions, **kw) -> FileContext:
    base = {
        "file_path": "src/app.py",
        "language": "python",
        "nloc": 100,
        "has_test_file": False,
        "module": None,
        "is_test": False,
        "all_functions": tuple(functions),
    }
    base.update(kw)
    return FileContext(**base)


def _one(ctx: FileContext):
    results = BIOMARKER.detect(ctx)
    assert len(results) == 1, results
    return results[0]


def test_an_untested_cc_of_five_fires_estimated_and_medium() -> None:
    r = _one(_ctx([_fn("f", 1, 20, 5)]))
    assert r.severity.value == "medium"
    assert r.details["coverage_basis"] == "estimated"
    assert r.details["coverage_tier"] == "none"
    assert "coverage_pct" not in r.details
    assert "%" not in r.reason


def test_apex_makes_no_claim_that_no_test_reaches_it() -> None:
    # Apex tests reach code through platform dispatch the graph does not see.
    apex = {"file_path": "classes/Batch.cls", "language": "apex"}
    assert BIOMARKER.detect(_ctx([_fn("execute", 1, 20, 5)], **apex)) == []
    # A test the graph does see still prices it.
    reached = _ctx([_fn("execute", 1, 20, 20)], function_test_hops={1: 2}, **apex)
    assert _one(reached).details["coverage_tier"] == "reached"


def test_below_the_bar_is_silent() -> None:
    assert BIOMARKER.detect(_ctx([_fn("f", 1, 20, 4)])) == []


def test_a_directly_tested_function_is_estimated_low() -> None:
    ctx = _ctx([_fn("f", 1, 20, 10)], function_test_hops={1: 1})
    assert BIOMARKER.detect(ctx) == []


def test_the_estimate_caps_at_medium() -> None:
    r = _one(_ctx([_fn("f", 1, 40, 20)]))  # CRAP 420 estimated
    assert r.severity.value == "medium"


@pytest.mark.parametrize(
    ("ccn", "severity"),
    [(5, "medium"), (7, "high"), (10, "critical")],  # CRAP 30, 56, 110 at 0%
)
def test_measured_severity_bands(ccn: int, severity: str) -> None:
    ctx = _ctx(
        [_fn("f", 1, 20, ccn)],
        line_coverage_pct=0.0,
        covered_lines=set(),
        coverable_lines=set(range(1, 21)),
        total_coverable_lines=20,
    )
    r = _one(ctx)
    assert r.severity.value == severity
    assert r.details["coverage_basis"] == "measured"
    assert r.details["coverage_pct"] == 0.0
    assert "coverage_tier" not in r.details


def test_measured_full_coverage_clears_a_moderate_function() -> None:
    lines = set(range(1, 21))
    ctx = _ctx(
        [_fn("f", 1, 20, 10)],
        line_coverage_pct=100.0,
        covered_lines=lines,
        coverable_lines=lines,
        total_coverable_lines=20,
    )
    assert BIOMARKER.detect(ctx) == []


def test_a_report_without_line_sets_falls_back_to_the_estimate() -> None:
    """A pinned percentage with no lines says nothing about one function."""
    ctx = _ctx([_fn("f", 1, 20, 6)], line_coverage_pct=80.0, total_coverable_lines=20)
    assert _one(ctx).details["coverage_basis"] == "estimated"


def test_a_file_the_report_ran_none_of_is_measured_at_zero() -> None:
    ctx = _ctx([_fn("f", 1, 20, 7)], line_coverage_pct=0.0, total_coverable_lines=20)
    r = _one(ctx)
    assert (r.details["coverage_basis"], r.severity.value) == ("measured", "high")


def test_a_hit_line_set_without_executable_lines_keeps_the_estimate() -> None:
    """Only hit lines named: silent about the function, not zero."""
    ctx = _ctx(
        [_fn("f", 10, 30, 7)], line_coverage_pct=40.0, covered_lines={1, 2}, total_coverable_lines=5
    )
    assert _one(ctx).details["coverage_basis"] == "estimated"


def test_a_paired_test_file_counts_as_reach() -> None:
    r = _one(_ctx([_fn("f", 1, 20, 12)], has_test_file=True))
    assert r.details["coverage_tier"] == "reached"


def test_complexity_of_thirty_steers_to_refactoring() -> None:
    lines = set(range(1, 61))
    ctx = _ctx(
        [_fn("f", 1, 60, 30)],
        line_coverage_pct=100.0,
        covered_lines=lines,
        coverable_lines=lines,
        total_coverable_lines=60,
    )
    r = _one(ctx)
    assert r.details["action"] == "refactor"
    assert "simplify" in r.reason
    assert _one(_ctx([_fn("g", 1, 20, 6)])).details["action"] == "add_tests"


def test_no_finding_on_test_files() -> None:
    assert BIOMARKER.detect(_ctx([_fn("f", 1, 20, 12)], file_path="tests/test_app.py", is_test=True)) == []


def test_the_stored_flag_decides_for_a_test_named_production_module() -> None:
    ctx = _ctx([_fn("f", 1, 20, 12)], file_path="core/test_selection.py", is_test=False)
    assert len(BIOMARKER.detect(ctx)) == 1


# --------------------------------------------------------------------------
# Engine wiring: the direct tier comes from the call graph
# --------------------------------------------------------------------------

# Complexity 12: only the direct tier clears it (reach alone estimates CRAP 43).
_FLAGS = "abcdefghijk"
_BRANCHY = (
    f"def route({', '.join(_FLAGS)}):\n"
    + "".join(f"    if {flag}:\n        return {i}\n" for i, flag in enumerate(_FLAGS, 1))
    + "    return 0\n"
)


def _tree(tmp_path):
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "router.py").write_text(_BRANCHY, encoding="utf-8")
    (tmp_path / "tests" / "test_router.py").write_text(
        "from src.router import route\n\n\ndef test_it():\n    assert route(*[1] * 11) == 1\n",
        encoding="utf-8",
    )
    return [parse_file(f, (tmp_path / f.path).read_bytes()) for f in FileTraverser(tmp_path).traverse()]


def _graph(*, calls: bool, via_helper: bool = False) -> nx.DiGraph:
    g = nx.DiGraph()
    for path, name, start, end in (
        ("tests/test_router.py", "test_it", 4, 5),
        ("src/router.py", "route", 1, 24),
        ("src/glue.py", "relay", 1, 2),
    ):
        sid = f"{path}::{name}"
        g.add_node(sid, node_type="symbol", name=name, file_path=path, start_line=start, end_line=end)
        g.add_edge(path, sid, edge_type="defines")
    if via_helper:
        g.add_edge("tests/test_router.py::test_it", "src/glue.py::relay", edge_type="calls")
        g.add_edge("src/glue.py::relay", "src/router.py::route", edge_type="calls")
    elif calls:
        g.add_edge("tests/test_router.py::test_it", "src/router.py::route", edge_type="calls")
    return g


def _crap_findings(parsed, graph):
    report = HealthAnalyzer(graph, parsed_files=parsed).analyze()
    return [f for f in report.findings if f.biomarker_type == "untested_complexity"]


def test_a_test_calling_the_function_clears_it(tmp_path) -> None:
    assert _crap_findings(_tree(tmp_path), _graph(calls=True)) == []


def test_without_the_call_the_same_function_fires(tmp_path) -> None:
    found = _crap_findings(_tree(tmp_path), _graph(calls=False))
    assert [(f.file_path, f.function_name) for f in found] == [("src/router.py", "route")]
    # The test imports the module, so the file is paired: reach, not a direct test.
    assert found[0].details["coverage_tier"] == "reached"


def test_a_stored_report_measures_the_function(tmp_path) -> None:
    from repowise.core.analysis.health.coverage.model import coverage_map_entry, file_coverage

    lines = range(1, 25)
    for covered, expected in ((lines, []), ((), ["measured"])):
        entry = coverage_map_entry(file_coverage("src/router.py", covered, lines), "lcov")
        report = HealthAnalyzer(
            _graph(calls=False),
            parsed_files=_tree(tmp_path / str(len(expected))),
            coverage_map={"src/router.py": entry},
        ).analyze()
        found = [f for f in report.findings if f.biomarker_type == "untested_complexity"]
        assert [f.details["coverage_basis"] for f in found] == expected


def test_a_test_two_calls_away_is_reach_not_a_direct_test(tmp_path) -> None:
    found = _crap_findings(_tree(tmp_path), _graph(calls=False, via_helper=True))
    assert [f.details["coverage_tier"] for f in found] == ["reached"]
