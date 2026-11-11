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

Which tests run is the per-language rule the health walk already uses
(``complexity.test_case``), read off the symbol's name. A language whose cases
that rule cannot tell from a name (JUnit's ``@Test``, Rust's ``#[test]``, a
language with no rule) seeds nothing, so its functions are left unmapped rather
than called untested. A mapped function with no test is one the graph saw
called, by resolved edges, and no test reached within the depth; a function
with no resolved caller at all (dispatch by name, a framework hook) is left
unmapped too. Rows two and three hops out are stored for readers that want
reach; plans quote only the tests one hop away.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath

from repowise.core.analysis.execution_graph import ExecutionGraphIndex, file_of_symbol
from repowise.core.analysis.health.complexity.test_case import (
    runs_module_callbacks,
    test_case_by_name,
)
from repowise.core.ingestion.models import EXTENSION_TO_LANGUAGE
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
    """A test that runs a function: its runnable selector and the call hops to it.

    A selector is a symbol id, which is a pytest node id without parameters
    (pytest runs every parametrization of it), or a JS/TS test file, whose
    ``it`` cases have no symbols of their own.
    """

    selector: str
    hops: int


FunctionTests = dict[str, tuple[MappedTest, ...]]


@lru_cache(maxsize=4096)
def _language(path: str) -> str | None:
    return EXTENSION_TO_LANGUAGE.get(PurePosixPath(path).suffix)


@lru_cache(maxsize=64)
def _seeds_tests(language: str | None) -> bool:
    """Whether the language's test cases can be found from the graph alone."""
    return test_case_by_name("", language) is not None or runs_module_callbacks(language)


def runnable_selector(symbol_id: str) -> str | None:
    """The selector that runs test-file symbol *symbol_id*, or ``None`` for a helper."""
    path, _, rest = symbol_id.partition("::")
    if not rest:
        return None
    language = _language(path)
    name = rest.rsplit("::", 1)[-1]
    if name == _MODULE:
        return path if runs_module_callbacks(language) else None
    return symbol_id if test_case_by_name(name, language) else None


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


def _offer(
    best: dict[str, dict[str, int]], callee: str, selectors: set[str], hops: int, k: int
) -> tuple[str, ...]:
    """Add to *callee*'s nearest the closest of *selectors* it has room for; the gain."""
    known = best.setdefault(callee, {})
    room = k - len(known)
    fresh = [selector for selector in selectors if selector not in known]
    if room <= 0 or not fresh:
        return ()
    fresh.sort(key=lambda selector: _affinity(callee, selector))
    gained = tuple(fresh[:room])
    known.update(dict.fromkeys(gained, hops))
    return gained


def function_test_map(
    index: ExecutionGraphIndex,
    test_files: Collection[str],
    *,
    k: int = FUNCTION_TEST_K,
) -> FunctionTests:
    """Each mapped non-test symbol's *k* nearest tests, nearest first.

    A symbol is mapped when a test reaches it, or when it has a resolved caller
    and its language seeds tests: then an empty tuple means no test reaches it.
    """
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
            if gained := _offer(best, callee, selectors, hops, k):
                frontier[callee] = gained
    out: FunctionTests = {
        node: tuple(
            MappedTest(selector, hops)
            for selector, hops in sorted(
                known.items(), key=lambda item, n=node: (item[1], *_affinity(n, item[0]))
            )
        )
        for node, known in best.items()
        if file_of_symbol(node) not in tests
    }
    for node in index.reverse:
        path = file_of_symbol(node)
        if node not in out and path not in tests and _seeds_tests(_language(path)):
            out[node] = ()
    return out


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


def encoded_map(tests: Mapping[str, tuple[MappedTest, ...]]) -> dict[str, str]:
    """Each mapped symbol's stored form, encoded once per run; an absent symbol
    stores NULL (never mapped)."""
    return {symbol: encode_tests(mapped) for symbol, mapped in tests.items()}


__all__ = [
    "FUNCTION_TEST_K",
    "FunctionTests",
    "MappedTest",
    "decode_tests",
    "encode_tests",
    "encoded_map",
    "function_test_map",
    "runnable_selector",
]
