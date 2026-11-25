"""Benchmarks speak as benchmarks: the function each measures and its numbers (no store)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.evidence.benchmarks import benchmark_view, measured_function, measured_functions


@pytest.mark.parametrize(
    ("benchmark", "callees", "measures"),
    [
        # gin: the benchmark named for the function it calls.
        (
            "context_test.go::BenchmarkGetMapFromFormData",
            ["context.go::getMapFromFormData"],
            "context.go::getMapFromFormData",
        ),
        # Word order and an extra word for the case.
        (
            "path_test.go::BenchmarkPathCleanLong",
            ["path.go::cleanPath", "path_test.go::gen"],
            "path.go::cleanPath",
        ),
        ("utils_test.go::BenchmarkParseAccept", ["utils.go::parseAccept"], "utils.go::parseAccept"),
        # Setup and a request helper only: no claim.
        (
            "benchmarks_test.go::Benchmark5Params",
            ["benchmarks_test.go::runRequest", "gin.go::Engine::Use", "gin.go::New"],
            None,
        ),
        # One word of three is not what the benchmark measures.
        (
            "benchmarks_test.go::BenchmarkOneRouteJSON",
            ["context.go::Context::JSON", "gin.go::New"],
            None,
        ),
        # A single production callee whose name the benchmark does not carry.
        ("binding/v_test.go::BenchmarkSliceValidationError", ["gin.go::New"], None),
        # pytest-benchmark: the class names the subject, the method the call.
        (
            "benchmarks/test_io.py::TestCache::test_get",
            ["io/cache.py::Cache::get"],
            "io/cache.py::Cache::get",
        ),
        (
            "benchmarks/test_io.py::test_parse_json",
            ["io/parse.py::parse_json", "io/parse.py::parse"],
            "io/parse.py::parse_json",
        ),
        (None, ["a.py::f"], None),
        ("a_test.go::Benchmark", ["a.go::f"], None),
    ],
)
def test_measured_function(benchmark, callees, measures) -> None:
    assert measured_function(benchmark, callees) == measures


def test_a_tie_between_callees_is_no_answer() -> None:
    callees = ["a.go::readFile", "b.go::fileRead"]
    assert measured_function("a_test.go::BenchmarkReadFile", callees) is None
    assert measured_functions(["a_test.go::BenchmarkReadFile", None], {}) == {}


def _run(rid: str, name: str, day: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid,
        workload_name=name,
        environment="ci",
        measured_at=None,
        ingested_at=datetime(2026, 11, day, tzinfo=UTC),
        commit_sha="c" * 40,
    )


def _result(rid: str, key: str, symbol: str | None, median: float, allocs: float | None = None):
    return SimpleNamespace(
        run_id=rid,
        benchmark_key=key,
        symbol_id=symbol,
        median=median,
        unit="ns",
        allocs_per_op=allocs,
        bytes_per_op=None,
    )


def test_benchmark_view_reads_each_workloads_newest_run_with_named_functions_first() -> None:
    sym = "context_test.go::BenchmarkGetMapFromFormData"
    runs = [_run("old", "go bench", 1), _run("new", "go bench", 2)]
    results = [
        _result("old", "github.com/x/gin.BenchmarkGetMapFromFormData/Small", sym, 999.0, 9),
        _result(
            "new", "github.com/x/gin.Benchmark404", "benchmarks_test.go::Benchmark404", 62.6, 0
        ),
        _result("new", "github.com/x/gin.BenchmarkGetMapFromFormData/Small", sym, 105.1, 1),
        _result("new", "github.com/x/gin.BenchmarkUnresolved", None, 5.0),
    ]
    view = benchmark_view(runs, results, {sym: "context.go::getMapFromFormData"}, limit=2)
    assert view is not None
    assert (view["run_ids"], view["total"]) == (["new"], 3)
    assert view["rows"] == [
        {
            "run_id": "new",
            "benchmark": "BenchmarkGetMapFromFormData/Small",
            "benchmark_symbol_id": sym,
            "measures": "context.go::getMapFromFormData",
            "time": 105.1,
            "unit": "ns",
            "allocs_per_op": 1,
            "bytes_per_op": None,
        },
        {
            "run_id": "new",
            "benchmark": "Benchmark404",
            "benchmark_symbol_id": "benchmarks_test.go::Benchmark404",
            "measures": None,
            "time": 62.6,
            "unit": "ns",
            "allocs_per_op": 0,
            "bytes_per_op": None,
        },
    ]
    # ``limit=0`` keeps the runs and the total.
    assert benchmark_view(runs, results, {}, limit=0)["rows"] == []
    assert benchmark_view([], [], {}, limit=5) is None
