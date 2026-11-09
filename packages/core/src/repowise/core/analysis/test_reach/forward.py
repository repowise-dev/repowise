"""The forward walk and file pairing: which files some test reaches or pairs with, at all.

The batch question over a whole repository, not the attributed one the reverse
walks answer.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import PurePosixPath
from typing import Any, TypeAlias

from repowise.core.analysis.dead_code.file_reachability import BARREL_FILENAMES
from repowise.core.analysis.execution_graph import ExecutionGraphIndex, file_of_symbol
from repowise.core.ingestion.models import FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.test_paths import paired_test_names

from .model import DEFAULT_CALL_DEPTH

CallGraphView: TypeAlias = ExecutionGraphIndex


def call_graph_from_graph(graph: Any) -> CallGraphView:
    """Build the call-reachability view from an in-memory NetworkX graph.

    ``defines`` alone bridges files to symbols: measured across this repository
    it reaches every symbol, class members included, so also walking
    ``has_method`` would add a second containment layer for nothing.

    Returns an empty view for ``None`` - the health engine runs without a graph
    on some paths, and the documented outcome there is "no signal".
    """
    return ExecutionGraphIndex(graph)


def files_reached_by_tests(
    view: CallGraphView,
    test_files: set[str],
    *,
    max_depth: int = DEFAULT_CALL_DEPTH,
) -> set[str]:
    """Every non-test file some test can execute into within *max_depth* hops.

    One multi-source breadth-first search from every symbol the test files
    declare, so the cost is O(V+E) however many test files there are - this is
    the batch question ("which files are reached at all"), not the attributed
    one.

    Returns the reached set with the test files themselves removed: a test does
    not need a test.
    """
    if not test_files or max_depth < 1:
        return set()

    frontier: set[str] = set()
    for path in test_files:
        frontier.update(view.declares.get(path, ()))
    seen = set(frontier)
    # The seeds are what the tests *declare*, not what they reach. Counting them
    # would make containment alone a claim of reaching.
    reached: set[str] = set()
    for _ in range(max_depth):
        nxt: set[str] = set()
        for node in frontier:
            nxt.update(view.calls.get(node, ()))
        nxt -= seen
        if not nxt:
            break
        seen |= nxt
        reached |= nxt
        frontier = nxt
    return {file_of_symbol(symbol) for symbol in reached} - test_files


def _dependencies_of(graph: Any, path: str) -> set[str]:
    try:
        return {
            dst
            for _src, dst, data in graph.out_edges(path, data=True)
            if data.get("edge_type") in FILE_DEPENDENCY_EDGE_TYPES
        }
    except Exception:
        return set()


def files_with_paired_tests(
    graph: Any, paths: Collection[str], test_files: Collection[str]
) -> set[str]:
    """Files among *paths* that have a test of their own, import graph first.

    A test file that imports a file directly is its test, wherever either
    lives: ``tests/unit/cli/test_init.py`` importing ``cli/main.py`` pairs
    them although no name says so. A test *named* for a file
    (:func:`paired_test_names`) still pairs, but only when the graph agrees
    or cannot say: the named test imports the file directly or through a
    re-export barrel, has no resolved dependency at all, or there is no graph.
    Otherwise the name is a collision - ``distill/test_engine.py`` tests the
    distill engine, not ``health/engine.py`` - and does not pair.

    One hop, plus one more through a re-export barrel: a test importing a
    package's ``index.ts`` or ``__init__.py`` pairs the files that barrel
    imports, since that is how a package's tests reach its modules. A
    file-level "some test imports this", never a quantity. The reach walk
    above stays call-graph-only; pairing is the looser question "does this
    file have a test", and against this repository's stored line coverage
    direct-import pairing finds 71.6% of covered files at 98.9% precision
    where names alone found 34.6% at 100%.

    Last, a file a test loads through its own package pairs too
    (:func:`~.walk.package_walk`), and so does one a test reaches through
    code that calls into it (:func:`~.walk.use_walk`): the same bar
    ``pr_blast.file_tests`` clears before it calls a file untested, so the
    health surfaces and the review surfaces agree about which files have no
    test.
    """
    barrel_deps: dict[str, set[str]] = {}

    def imports_of(test: str) -> set[str]:
        direct = _dependencies_of(graph, test)
        through: set[str] = set()
        for mid in direct:
            if PurePosixPath(mid).name in BARREL_FILENAMES:
                if mid not in barrel_deps:
                    barrel_deps[mid] = _dependencies_of(graph, mid)
                through |= barrel_deps[mid]
        return direct | through

    paired: set[str] = set()
    deps: dict[str, set[str]] = {}
    if graph is not None:
        for test in test_files:
            deps[test] = imports_of(test)
            paired.update(deps[test])
    paired &= set(paths)

    by_name: dict[str, list[str]] = {}
    for path in {*paths, *test_files}:
        by_name.setdefault(path.rsplit("/", 1)[-1], []).append(path)

    def named_pair_holds(test: str, path: str) -> bool:
        if graph is None:
            return True
        imported = deps[test] if test in deps else imports_of(test)
        return not imported or path in imported

    for path in paths:
        if path in paired:
            continue
        candidates = (c for name in paired_test_names(path) for c in by_name.get(name, ()))
        if any(c != path and named_pair_holds(c, path) for c in candidates):
            paired.add(path)
    rest = [p for p in paths if p not in paired]
    if graph is not None and rest:
        paired |= _package_reached(graph, rest, test_files)
    return paired


def _package_reached(graph: Any, paths: list[str], test_files: Collection[str]) -> set[str]:
    """The *paths* a test loads through their own package, or reaches through code calling into them.

    The last two tiers ``pr_blast.file_tests`` runs before it calls a file
    untested (:func:`~.walk.package_walk`, :func:`~.walk.use_walk`), over the
    in-memory graph.
    """
    from dataclasses import replace

    from .maps import build_walk_maps, run
    from .walk import package_walk, use_walk

    try:
        maps = replace(build_walk_maps(graph), test_files=frozenset(test_files))
    except Exception:
        return set()
    found = set(run(package_walk(paths, test_files, rank=False), maps))
    rest = [p for p in paths if p not in found]
    if rest:
        found |= set(run(use_walk(rest, test_files, rank=False), maps))
    return found
