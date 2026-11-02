"""The actions a coverage finding carries: the test to extend, the prompt, the suppress rule.

Raised only from a measured report or a test that calls the function, never
because the graph found no test.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.coverage_gap import BIOMARKER as COVERAGE_GAP
from repowise.core.analysis.health.biomarkers.untested_complexity import BIOMARKER as CRAP
from repowise.core.analysis.health.complexity.models import FunctionComplexity
from repowise.core.analysis.health.config import HealthConfig
from repowise.core.analysis.health.coverage_actions import (
    coverage_actions,
    nearest_test_file,
    paired_test_index,
    suppress_how,
)

_TESTS = paired_test_index(
    ["tests/test_router.py", "tests/test_router_golden.py", "web/router.test.ts", "tests/test_other.py"]
)


# --------------------------------------------------------------------------
# The test to extend
# --------------------------------------------------------------------------


def test_the_nearest_mapped_test_wins() -> None:
    selector = "tests/integration/test_flow.py::test_routes"
    assert nearest_test_file("src/router.py", selector, _TESTS) == "tests/integration/test_flow.py"


def test_a_js_selector_is_the_test_file_itself() -> None:
    assert nearest_test_file("web/router.ts", "web/router.test.ts", _TESTS) == "web/router.test.ts"


def test_without_a_mapped_test_a_test_named_for_the_file() -> None:
    assert nearest_test_file("src/router.py", None, _TESTS) == "tests/test_router.py"
    assert nearest_test_file("web/router.ts", None, _TESTS) == "web/router.test.ts"


def test_nothing_named_for_it_names_nothing() -> None:
    assert nearest_test_file("src/billing.py", None, _TESTS) is None


# --------------------------------------------------------------------------
# The suppress rule is the real one
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["src/legacy/foo.py", "packages/web/src/app/repos/[id]/tests/page.tsx", "setup.py", "a b.py"],
)
def test_the_suppress_rule_silences_exactly_that_file(path: str) -> None:
    how = suppress_how("coverage_gap", path)
    file, _, rule = how.partition(": ")
    assert file == ".repowise/health-rules.json"
    cfg = HealthConfig.from_json(rule)
    # A root file stays anchored: ``pkg/setup.py`` is another file.
    disabled = cfg.per_file_disabled([path, "src/legacy/bar.py", "pkg/setup.py"])
    assert disabled == {path: {"coverage_gap"}}


# --------------------------------------------------------------------------
# Action shapes
# --------------------------------------------------------------------------


def test_add_tests_then_suppress() -> None:
    actions = coverage_actions(
        "coverage_gap", "src/router.py", function=None, target_test="tests/test_router.py",
        evidence="40/90 lines uncovered (56% line coverage)",
    )
    assert [a["kind"] for a in actions] == ["add-tests", "suppress"]
    add = actions[0]
    assert add["target_test"] == "tests/test_router.py"
    assert "Extend `tests/test_router.py`" in add["prompt"]
    assert "Add tests for `src/router.py`" in add["prompt"]
    assert "40/90 lines uncovered" in add["prompt"]


def test_no_target_omits_the_key_and_says_where_to_add() -> None:
    add = coverage_actions(
        "coverage_gap", "src/billing.py", function=None, target_test=None, evidence="x"
    )[0]
    assert "target_test" not in add
    assert "Add a test beside" in add["prompt"]


def test_refactor_replaces_add_tests() -> None:
    actions = coverage_actions(
        "untested_complexity", "src/router.py", function="route", target_test="tests/test_router.py",
        evidence="CRAP 30", refactor=True,
    )
    assert [a["kind"] for a in actions] == ["refactor", "suppress"]
    assert "`route` in `src/router.py`" in actions[0]["prompt"]


# --------------------------------------------------------------------------
# The biomarkers carry them only on evidence
# --------------------------------------------------------------------------


def _fn(start: int, end: int, ccn: int) -> FunctionComplexity:
    return FunctionComplexity(
        name="route", start_line=start, end_line=end, ccn=ccn, max_nesting=1, cognitive=ccn,
        nloc=end - start,
    )


def _ctx(**kw) -> FileContext:
    base = {
        "file_path": "src/router.py",
        "language": "python",
        "nloc": 200,
        "has_test_file": False,
        "module": None,
        "is_test": False,
    }
    base.update(kw)
    return FileContext(**base)


def test_a_coverage_gap_names_the_file_test() -> None:
    ctx = _ctx(line_coverage_pct=20.0, total_coverable_lines=100, file_test="tests/test_router.py")
    (finding,) = COVERAGE_GAP.detect(ctx)
    assert [a["kind"] for a in finding.details["actions"]] == ["add-tests", "suppress"]
    assert finding.details["actions"][0]["target_test"] == "tests/test_router.py"


def test_measured_crap_carries_actions_with_the_function_test() -> None:
    lines = set(range(1, 21))
    ctx = _ctx(
        all_functions=(_fn(1, 20, 7),),
        line_coverage_pct=0.0,
        coverable_lines=lines,
        total_coverable_lines=20,
        function_test_files={1: "tests/test_route_paths.py"},
        file_test="tests/test_router.py",
    )
    (finding,) = CRAP.detect(ctx)
    assert finding.details["actions"][0]["target_test"] == "tests/test_route_paths.py"


def test_a_directly_tested_function_carries_actions() -> None:
    ctx = _ctx(all_functions=(_fn(1, 60, 40),), function_test_hops={1: 1})
    (finding,) = CRAP.detect(ctx)
    assert finding.details["coverage_tier"] == "direct"
    assert [a["kind"] for a in finding.details["actions"]] == ["refactor", "suppress"]


@pytest.mark.parametrize(("hops", "reached"), [(None, False), (None, True), (2, False)])
def test_no_action_when_only_the_graph_speaks(hops, reached) -> None:
    """``none`` means the graph found nothing; ``reached`` is reach, not a test run."""
    ctx = _ctx(
        all_functions=(_fn(1, 40, 12),),
        reached_by_tests=reached,
        function_test_hops={1: hops} if hops else {},
        file_test="tests/test_router.py",
    )
    (finding,) = CRAP.detect(ctx)
    assert finding.details["coverage_basis"] == "estimated"
    assert "actions" not in finding.details


# --------------------------------------------------------------------------
# Engine wiring: the nearest test reaches the finding
# --------------------------------------------------------------------------


@pytest.mark.parametrize("calls", [True, False])
def test_the_engine_names_the_test_to_extend(tmp_path, calls: bool) -> None:
    """Through the function test map when a test calls it, else by name."""
    from repowise.core.analysis.health import HealthAnalyzer
    from repowise.core.analysis.health.coverage.model import coverage_map_entry, file_coverage
    from tests.unit.health.test_untested_complexity import _graph, _tree

    entry = coverage_map_entry(file_coverage("src/router.py", (), range(1, 25)), "lcov")
    report = HealthAnalyzer(
        _graph(calls=calls), parsed_files=_tree(tmp_path), coverage_map={"src/router.py": entry}
    ).analyze()
    (finding,) = [f for f in report.findings if f.biomarker_type == "untested_complexity"]
    assert finding.details["actions"][0]["target_test"] == "tests/test_router.py"
