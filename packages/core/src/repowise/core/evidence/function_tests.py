"""Which tests a measured function's row names, and the benchmark that checks a fix.

Pure, over rows a host already holds, so the engine and a hosted reader name
the same tests:

- :func:`row_tests`: the tests the per-test map saw run the function's lines
  when the map has its file, else the call graph's nearest tests, labelled so.
  A benchmark is never listed as a test.
- :func:`benchmark_command`: the command that reruns the benchmarks that
  measure the function, the way to verify a fix.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from pathlib import PurePosixPath
from typing import Any, NamedTuple

from repowise.core.code_origin import is_benchmark_function
from repowise.core.test_paths import nodeid_without_params, nodeid_without_phase

#: ``tests`` lists tests the per-test map saw run the function's lines.
PER_TEST_MAP = "per_test_map"
#: ``tests`` lists the function's nearest tests by call graph: no map has its file.
CALL_GRAPH = "call_graph"

#: Benchmarks one verify command reruns at most.
_MAX_BENCHMARKS = 3


class RowTests(NamedTuple):
    """A row's tests and what they rest on (``None`` and ``None``: unknown)."""

    tests: list[str] | None
    basis: str | None


def runnable_test(test_id: str, test_file: str | None) -> str:
    """A map test id as the selector the call graph's tests use.

    pytest-cov's phase and a parameter id are cut (every parametrization runs
    under one selector) and the file is the resolved test file; an id with no
    ``::`` names its file.
    """
    test_id = nodeid_without_params(nodeid_without_phase(test_id))
    if not test_file:
        return test_id
    _, sep, name = test_id.partition("::")
    return f"{test_file}::{name}" if sep else test_file


def is_benchmark(selector: str, stored: Collection[str] = ()) -> bool:
    """Whether *selector* is a benchmark: one with stored results, or one its
    runner collects as a benchmark (:func:`~repowise.core.code_origin.is_benchmark_function`)."""
    if selector in stored:
        return True
    path, sep, name = selector.partition("::")
    return bool(sep) and is_benchmark_function(path, name.rpartition("::")[2])


def row_tests(
    ran: Sequence[tuple[str, str | None, int]] | None,
    nearest: Sequence[Any] | None,
    *,
    k: int,
    benchmarks: Collection[str] = (),
) -> RowTests:
    """At most *k* tests for a measured function's row, and their basis.

    *ran* is what ``crud.tests_running_spans`` read for the function:
    ``(test_id, test_file, lines run)`` per test, or ``None`` when the map has
    no row for its file. *nearest* is its stored nearest tests by call graph
    (``MappedTest``, nearest first), or ``None`` when never mapped.

    With the map, its tests are the list: those the call graph also names
    first, in its order, then the ones that ran most of the function's lines.
    ``[]`` there means no measured test ran the function. Without it, the
    nearest tests by call graph stand in, labelled :data:`CALL_GRAPH`.
    Benchmarks (stored *benchmarks* or by name) are never listed.
    """
    near = [t.selector for t in nearest or ()]
    if ran is not None:
        lines: dict[str, int] = {}
        for test_id, test_file, count in ran:
            selector = runnable_test(test_id, test_file)
            if not is_benchmark(selector, benchmarks):
                lines[selector] = max(lines.get(selector, 0), count)
        place = {s: i for i, s in enumerate(near)}
        ordered = sorted(lines, key=lambda s: (place.get(s, len(place)), -lines[s], s))
        return RowTests(ordered[:k], PER_TEST_MAP)
    if nearest is None:
        return RowTests(None, None)
    return RowTests([s for s in near if not is_benchmark(s, benchmarks)][:k], CALL_GRAPH)


def benchmark_command(benchmarks: Iterable[str]) -> str | None:
    """The command that reruns *benchmarks* (selectors), run before and after a fix.

    Go: the ``Benchmark`` functions in the first one's package, anchored, five
    counts so the runs can be compared. Python: the pytest-benchmark node ids.
    Ceiling: other runners and a Go module below the repository root get no
    command; the package path is from the repository root.
    """
    ordered = sorted(set(benchmarks))
    first = next(
        (p for s in ordered if (p := s.partition("::")[0]).endswith(("_test.go", ".py"))), None
    )
    if first is None:
        return None
    if first.endswith("_test.go"):
        folder = str(PurePosixPath(first).parent)
        names = [
            s.rpartition("::")[2]
            for s in ordered
            if s.partition("::")[0].endswith("_test.go")
            and str(PurePosixPath(s.partition("::")[0]).parent) == folder
        ][:_MAX_BENCHMARKS]
        pattern = names[0] if len(names) == 1 else f"({'|'.join(names)})"
        package = "." if folder == "." else f"./{folder}"
        return f"go test -run '^$' -bench '^{pattern}$' -count 5 {package}"
    if first.endswith(".py"):
        tests = [s for s in ordered if s.partition("::")[0].endswith(".py")][:_MAX_BENCHMARKS]
        return f"pytest {' '.join(tests)} --benchmark-only"
    return None


__all__ = [
    "CALL_GRAPH",
    "PER_TEST_MAP",
    "RowTests",
    "benchmark_command",
    "is_benchmark",
    "row_tests",
    "runnable_test",
]
