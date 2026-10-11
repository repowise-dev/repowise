"""Finding types taken off default surfaces, and how the rest is worded.

- ``unused_internal``, ``duplicated_assertion_block`` and ``dry_violation`` are
  hidden in the finding-type registry.
- "N+1" is said only on a database boundary.
- ``serial_await_in_loop`` stays silent on a line ``io_in_loop`` reports.
- The long-parameter-list marker skips test cases.
- A whole unreachable file is never deletion-ready.
- The worst file is a production file; the worst test is reported apart.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.dead_code.risk_factors import (
    RISK_CAP_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
    effective_safe_to_delete,
)
from repowise.core.analysis.finding_registry import excluded_types, status_of
from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.io_in_loop import IoInLoopDetector
from repowise.core.analysis.health.biomarkers.primitive_obsession import (
    PrimitiveObsessionDetector,
)
from repowise.core.analysis.health.biomarkers.serial_await_in_loop import (
    SerialAwaitInLoopDetector,
)
from repowise.core.analysis.health.complexity import FunctionComplexity, PerfHit
from repowise.core.analysis.health.ranking import worst_metric
from repowise.core.analysis.health.scoring import compute_kpis


def _ctx(*, perf_hits=(), fns=()) -> FileContext:
    return FileContext(
        file_path="src/example.py",
        language="python",
        nloc=max(sum(f.nloc for f in fns), 100),
        has_test_file=False,
        module=None,
        all_functions=tuple(fns),
        perf_hits=list(perf_hits),
    )


def test_failing_types_are_hidden():
    for name in ("unused_internal", "duplicated_assertion_block", "dry_violation"):
        assert status_of(name).status == "hidden"
        assert name in excluded_types(requested=[name], include_provisional=True)
    # The rest of each family stays on.
    for name in ("unused_export", "unreachable_file", "io_in_loop", "complex_method"):
        assert name not in excluded_types()


def test_n_plus_one_only_with_a_database_boundary():
    hits = [
        PerfHit(kind="io_in_loop", line=3, function="f", detail="db"),
        PerfHit(kind="io_in_loop", line=5, function="f", detail="network"),
        PerfHit(kind="io_in_loop", line=7, function="f", detail="filesystem"),
        PerfHit(
            kind="io_in_loop", line=9, function="f", detail="network", path=("a.py::f", "b.py::g")
        ),
        PerfHit(kind="io_in_loop", line=11, function="f", detail="db", path=("a.py::f", "b.py::q")),
    ]
    by_line = {r.line_start: r for r in IoInLoopDetector().detect(_ctx(perf_hits=hits))}
    for line, result in by_line.items():
        has_db = result.details["boundary_kind"] == "db"
        assert ("N+1" in result.reason) is has_db, (line, result.reason)
        if not has_db:
            assert "I/O call inside a loop" in result.reason
    assert "cross-function N+1" in by_line[11].reason
    assert "cross-function I/O call inside a loop" in by_line[9].reason


def test_serial_await_suppressed_where_io_in_loop_fires():
    hits = [
        PerfHit(kind="io_in_loop", line=4, function="f", detail="network"),
        PerfHit(kind="serial_await_in_loop", line=4, function="f", detail="network"),
        PerfHit(kind="serial_await_in_loop", line=8, function="f", detail="network"),
    ]
    out = SerialAwaitInLoopDetector().detect(_ctx(perf_hits=hits))
    assert [r.line_start for r in out] == [8]


def test_long_parameter_list_skips_test_cases_and_says_what_it_is():
    wide = FunctionComplexity(
        "build", 1, 20, ccn=1, max_nesting=0, cognitive=0, nloc=14, param_count=7
    )
    test_case = FunctionComplexity(
        "test_build",
        30,
        50,
        ccn=1,
        max_nesting=0,
        cognitive=0,
        nloc=14,
        param_count=7,
        is_test_case=True,
    )
    filler = FunctionComplexity(
        "filler", 60, 130, ccn=1, max_nesting=0, cognitive=0, nloc=70, param_count=0
    )
    out = PrimitiveObsessionDetector().detect(_ctx(fns=[wide, test_case, filler]))
    assert [r.function_name for r in out] == ["build"]
    assert out[0].reason.startswith("long parameter list")


def test_unreachable_file_is_never_deletion_ready():
    assert not effective_safe_to_delete(1.0, "src/old.py", True, "unreachable_file")
    assert effective_safe_to_delete(1.0, "src/old.py", True, "unused_export")


def test_mcp_dead_code_tiers_are_the_engine_thresholds():
    from repowise.core.analysis.dead_code.serving import TIER_FLOORS

    assert TIER_FLOORS["high"] == SAFE_CONFIDENCE_THRESHOLD
    assert TIER_FLOORS["medium"] == RISK_CAP_CONFIDENCE


def _metric(path: str, score: float, *, is_test: bool) -> SimpleNamespace:
    return SimpleNamespace(file_path=path, score=score, nloc=10, is_test=is_test)


def test_worst_file_is_production_and_tests_report_apart():
    rows = [
        _metric("tests/test_a.py", 1.0, is_test=True),
        _metric("src/a.py", 3.0, is_test=False),
        _metric("src/b.py", 6.0, is_test=False),
    ]
    assert worst_metric(rows, {}).file_path == "src/a.py"
    kpis = compute_kpis(rows, set())
    assert kpis["worst_performer_path"] == "src/a.py"
    assert kpis["worst_test_path"] == "tests/test_a.py"
    assert kpis["worst_test_score"] == 1.0
    # A tests-only repo still names a worst file.
    only_tests = [_metric("tests/test_a.py", 2.0, is_test=True)]
    assert worst_metric(only_tests, {}).file_path == "tests/test_a.py"
