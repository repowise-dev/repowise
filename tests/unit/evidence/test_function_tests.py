"""``evidence.function_tests``: a measured row names the tests that ran it, else the nearest."""

from __future__ import annotations

from repowise.core.analysis.function_test_map import MappedTest
from repowise.core.evidence.function_tests import (
    CALL_GRAPH,
    PER_TEST_MAP,
    benchmark_command,
    is_benchmark,
    row_tests,
    runnable_test,
)

NEAR = (
    MappedTest("tests/test_options.py::test_help_option", 1),
    MappedTest("tests/test_basic.py::test_param_named_help", 2),
)


def test_map_ids_become_the_selectors_the_call_graph_uses() -> None:
    assert runnable_test("tests/test_x.py::test_f[1-a]|run", "tests/test_x.py") == (
        "tests/test_x.py::test_f"
    )
    assert runnable_test("test_x.py::TestC::test_f", "tests/test_x.py") == (
        "tests/test_x.py::TestC::test_f"
    )
    assert runnable_test("suite name", "tests/test_x.py") == "tests/test_x.py"
    assert runnable_test("tests/test_x.py::test_f", None) == "tests/test_x.py::test_f"


def test_the_map_lists_only_tests_that_ran_it_ones_the_graph_names_first() -> None:
    ran = [
        ("tests/test_a.py::test_wide", "tests/test_a.py", 9),
        ("tests/test_a.py::test_narrow[1]", "tests/test_a.py", 2),
        ("tests/test_a.py::test_narrow[2]", "tests/test_a.py", 4),
        ("tests/test_basic.py::test_param_named_help", "tests/test_basic.py", 1),
    ]
    got = row_tests(ran, NEAR, k=3)
    # The graph's static nearest test that is not in the map is left out.
    assert got == (
        [
            "tests/test_basic.py::test_param_named_help",
            "tests/test_a.py::test_wide",
            "tests/test_a.py::test_narrow",
        ],
        PER_TEST_MAP,
    )


def test_a_map_with_the_file_and_no_test_running_it_says_none_ran_it() -> None:
    assert row_tests([], NEAR, k=3) == ([], PER_TEST_MAP)


def test_without_the_map_the_nearest_tests_stand_in_labelled() -> None:
    assert row_tests(None, NEAR, k=1) == (["tests/test_options.py::test_help_option"], CALL_GRAPH)
    assert row_tests(None, (), k=3) == ([], CALL_GRAPH)
    assert row_tests(None, None, k=3) == (None, None)


def test_a_benchmark_is_never_a_test() -> None:
    ran = [
        ("context_test.go::BenchmarkGetMap", "context_test.go", 5),
        ("tests/test_app.py::test_timed", "tests/test_app.py", 3),
        ("context_test.go::TestGetMap", "context_test.go", 2),
    ]
    assert row_tests(ran, None, k=3, benchmarks={"tests/test_app.py::test_timed"}) == (
        ["context_test.go::TestGetMap"],
        PER_TEST_MAP,
    )
    near = (MappedTest("benches/bench_x.py::bench_parse", 1), MappedTest("t_test.go::TestX", 1))
    assert row_tests(None, near, k=3) == (["t_test.go::TestX"], CALL_GRAPH)


def test_benchmarks_by_stored_result_or_by_runner_name() -> None:
    assert is_benchmark("context_test.go::BenchmarkGetMap")
    assert not is_benchmark("context_test.go::Benchmarking")
    assert not is_benchmark("context.go::BenchmarkGetMap")
    assert is_benchmark("tests/test_a.py::test_t", {"tests/test_a.py::test_t"})
    assert not is_benchmark("tests/test_a.py::test_t")


def test_the_verify_command_reruns_the_benchmarks_that_measure_it() -> None:
    assert benchmark_command(["context_test.go::BenchmarkGetMapFromFormData"]) == (
        "go test -run '^$' -bench '^BenchmarkGetMapFromFormData$' -count 5 ."
    )
    assert (
        benchmark_command(
            [
                "binding/b_test.go::BenchmarkB",
                "binding/a_test.go::BenchmarkA",
                "other/o_test.go::BenchmarkO",
            ]
        )
        == "go test -run '^$' -bench '^(BenchmarkA|BenchmarkB)$' -count 5 ./binding"
    )
    assert benchmark_command(["benches/bench_x.py::bench_parse"]) == (
        "pytest benches/bench_x.py::bench_parse --benchmark-only"
    )
    assert benchmark_command([]) is None
    assert benchmark_command(["bench/x.bench.ts"]) is None
