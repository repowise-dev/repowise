"""Each function's nearest test functions, read off the call graph the health pass holds.

``test_reachability`` answers which test *files* reach a file. A plan that
changes one function wants something narrower: the few test functions that
run it, as selectors a test runner takes (``tests/x/test_a.py::TestC::test_b``),
so a check between edits runs seconds of tests rather than whole files.

One forward walk from every runnable test over the reliable execution edges
(``ExecutionGraphIndex.forward``), at most :data:`DEFAULT_CALL_DEPTH` hops, the
depth the file walk measured as saturating. Helpers inside test files are walked
through but never quoted: only a symbol a runner collects is a selector. Each
node keeps its :data:`FUNCTION_TEST_K` nearest tests and passes on only the ones
it just gained, so the cost is O(edges x k x depth) however large the suite.
That is exact by hops: if a test is among a function's k nearest through some
caller, either it is among that caller's k nearest one hop closer, or the
caller already holds k tests at least as close.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath

from repowise.core.analysis.execution_graph import ExecutionGraphIndex, file_of_symbol
from repowise.core.test_paths import paired_test_names

# How many tests each function keeps. Measured on this repository and hermes:
# recall against direct callers gains under 4 points past 5 while the stored
# bytes grow by half again at 8.
FUNCTION_TEST_K = 5

_MODULE = "__module__"
# A shorter name matches inside unrelated test names (``get`` in ``test_target``).
_MIN_NAME_MATCH = 3


@dataclass(frozen=True, slots=True)
class MappedTest:
    """A test that runs a function: its runnable selector and the call hops to it."""

    selector: str
    hops: int


FunctionTests = dict[str, tuple[MappedTest, ...]]


def runnable_selector(symbol_id: str) -> str | None:
    """The selector that runs test-file symbol *symbol_id*, or ``None`` for a helper.

    A ``test``-named function or method is collected by pytest, Go and JUnit 3
    naming; a Python method only inside a ``Test`` class. A JS/TS suite's ``it``
    callbacks have no symbol of their own, so its calls sit on the module, and
    the file is the selector. Ceiling: an annotation-only test (JUnit 5
    ``@Test void parses()``) is not seen; reading the annotation is the upgrade.
    """
    path, _, rest = symbol_id.partition("::")
    if not rest:
        return None
    parts = rest.split("::")
    if parts[-1] == _MODULE:
        return None if path.endswith(".py") else path
    if not parts[-1].lower().startswith("test"):
        return None
    if path.endswith(".py") and len(parts) > 1 and not parts[-2].startswith("Test"):
        return None
    return symbol_id


@lru_cache(maxsize=65536)
def _paired(file_path: str) -> frozenset[str]:
    return paired_test_names(file_path)


def _affinity(function_id: str, selector: str) -> tuple[int, int, str]:
    """Among tests the same hops away: one naming the function, then one in a
    file named for the function's file, then by selector."""
    name = function_id.rsplit("::", 1)[-1].lstrip("_").lower()
    test_name = selector.rsplit("::", 1)[-1].lower()
    names_it = len(name) >= _MIN_NAME_MATCH and name in test_name
    test_file = PurePosixPath(file_of_symbol(selector)).name
    return (
        0 if names_it else 1,
        0 if test_file in _paired(file_of_symbol(function_id)) else 1,
        selector,
    )


def _seeds(index: ExecutionGraphIndex, test_files: Iterable[str]) -> dict[str, str]:
    """Every runnable test symbol, mapped to its selector."""
    seeds: dict[str, str] = {}
    for path in test_files:
        for symbol in index.declares.get(path, ()):
            selector = runnable_selector(symbol)
            if selector is not None:
                seeds[symbol] = selector
    return seeds


def function_test_map(
    index: ExecutionGraphIndex,
    test_files: Collection[str],
    *,
    k: int = FUNCTION_TEST_K,
) -> FunctionTests:
    """Each non-test symbol some test reaches, with its *k* nearest tests, nearest first."""
    # Here, not at the top: the store imports this module, and the reach module
    # imports the store's models.
    from repowise.core.analysis.test_reachability import DEFAULT_CALL_DEPTH

    tests = set(test_files)
    frontier = {symbol: (selector,) for symbol, selector in _seeds(index, tests).items()}
    best: dict[str, dict[str, int]] = {}
    for hops in range(1, DEFAULT_CALL_DEPTH + 1):
        offers: dict[str, set[str]] = {}
        for node, selectors in frontier.items():
            for callee in index.forward.get(node, ()):
                offers.setdefault(callee, set()).update(selectors)
        frontier = {}
        for callee, selectors in offers.items():
            known = best.setdefault(callee, {})
            fresh = [selector for selector in selectors if selector not in known]
            room = k - len(known)
            if room <= 0 or not fresh:
                continue
            fresh.sort(key=lambda selector, c=callee: _affinity(c, selector))
            gained = fresh[:room]
            known.update(dict.fromkeys(gained, hops))
            frontier[callee] = tuple(gained)
    return {
        node: tuple(
            MappedTest(selector, hops)
            for selector, hops in sorted(
                known.items(), key=lambda item, n=node: (item[1], *_affinity(n, item[0]))
            )
        )
        for node, known in best.items()
        if file_of_symbol(node) not in tests
    }


def encode_tests(tests: Iterable[MappedTest]) -> str:
    """The stored form: ``[[selector, hops], ...]``, compact."""
    return json.dumps([[test.selector, test.hops] for test in tests], separators=(",", ":"))


def decode_tests(value: str | None) -> tuple[MappedTest, ...] | None:
    """The stored form back; ``None`` when the row was never mapped or is unreadable."""
    if value is None:
        return None
    try:
        rows = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    return tuple(
        MappedTest(row[0], row[1])
        for row in rows
        if isinstance(row, list)
        and len(row) == 2
        and isinstance(row[0], str)
        and isinstance(row[1], int)
    )


def stamped_tests(tests: Mapping[str, tuple[MappedTest, ...]], symbol_id: str) -> str:
    """What a mapped run stores for *symbol_id*: its tests, ``[]`` when none reach it."""
    return encode_tests(tests.get(symbol_id, ()))


__all__ = [
    "FUNCTION_TEST_K",
    "FunctionTests",
    "MappedTest",
    "decode_tests",
    "encode_tests",
    "function_test_map",
    "runnable_selector",
    "stamped_tests",
]
