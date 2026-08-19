"""Static test-to-code map, derived from the dependency graph.

``test_coverage`` answers "which test executed this line" and is filled only by
a coverage report. Most repositories have none, so ``tests_to_run`` came back
empty, ``impacted_tests`` said "run the full suite", and ``untested_hotspot``
fell back to matching filenames.

Matching filenames fails both ways, and this repository is the proof. Of its six
worst bug-magnet files, five - ``call_resolver.py``, ``dead_code/analyzer.py``,
``pipeline/persist.py``, ``tool_answer/answer.py``, ``pr_blast.py`` - have no
file named for them anywhere under ``tests/`` and so read as untested, while the
graph names 7, 9, 23, 18 and 3 test files importing them. The sixth,
``analysis/health/engine.py``, is worse: the convention matches on basename
alone, so it paired with ``tests/unit/distill/test_engine.py`` - a different
engine, in a different subsystem - and called the file tested on the strength of
a name collision. The graph names the six ``tests/unit/health/`` files that
actually import it.

The graph already knows the answer in the shape it can honestly give: a test
file that imports a source file *reaches* it. This module reads that relation.
It is a second signal beside the measured one, never a replacement and never
averaged with it.

What it claims, and what it does not
------------------------------------
Reaching is not executing. A test that imports a module pulls in every symbol
the module defines, while the test body may touch one function; the inferred
map therefore **over-claims**, and the direction of the error is known and
one-sided:

* Sound as a floor. "Some test reaches this file, so do not call it untested"
  is safe: the import is recorded, not guessed.
* Unsound as a quantity. No percentage may be derived from it. There is no line
  attribution here at all - reaching is a file-level fact - so a caller that
  needs "are these changed *lines* covered" must use the measured map or say it
  does not know.

Every surface that consumes this labels the result ``inferred``. Nothing here
writes to ``test_coverage``, and no row it produces is stored: the relation is
a bounded walk over ``graph_edges``/``graph_nodes``, which are already indexed
and already fresh. Materialising it would cost a transitive closure - on this
repository tests reach 1,630 of 2,509 production files, so the stored form is
O(tests x sources) rows that go stale the moment the graph moves, to answer a
query that is a breadth-first search over data already in the database.

Depth
-----
``max_depth`` bounds the chain, and the default of 1 was measured rather than
picked. Dogfooded against a real ``coverage run --contexts=test`` on this
repository, over a slice where per-test attribution was complete and both sides
saw the same 38 test files:

===========  =========  =========  ==========  ==========
max_depth    reach      reach      run-list    run-list
             precision  recall     precision   hit rate
===========  =========  =========  ==========  ==========
1            72.1%      30.4%      93.1%       96.8%
2            40.7%      45.1%      73.8%       97.9%
3            17.1%      45.1%      -           -
===========  =========  =========  ==========  ==========

Both uses want precision. A false "something reaches this" suppresses a real
untested-hotspot finding, and a false entry in a run-list sends someone to run
a test that cannot fail for their change; the extra recall a second hop buys is
not worth halving either. Depth 3 is where the closure starts to blow up (269
claims for the same 46 confirmations) and is never a sensible default.

Recall at depth 1 already beats the filename convention it sits beside (30.4%
against 23.5% on the same set) while resting on a recorded edge instead of a
name, and the two are unioned rather than ranked, so the shipped floor is
better than either.

A repository whose tests import a package facade rather than the module under
test will want ``max_depth=2``; it is a keyword argument on both walks for
exactly that reason.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ingestion.models import FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.persistence.models import GraphNode

# How many dependency hops a test may take and still be said to reach a file.
# 1 - a test imports the file - is the measured choice, not a conservative
# guess; see the Depth section above for the numbers behind it.
DEFAULT_MAX_DEPTH = 1

# Cap on how many test files one target reports. The consumers all cut their
# own lists shorter; the cap exists so a facade module imported by every test
# in the suite cannot produce an unbounded intermediate.
MAX_TESTS_PER_TARGET = 50

__all__ = [
    "DEFAULT_MAX_DEPTH",
    "MAX_TESTS_PER_TARGET",
    "files_reached_by_tests",
    "forward_dependencies_from_graph",
    "load_test_files",
    "tests_reaching",
]


def files_reached_by_tests(
    forward_deps: dict[str, set[str]],
    test_files: set[str],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> set[str]:
    """Every non-test file some test reaches within *max_depth* hops.

    One multi-source breadth-first search from the whole test set at once, so
    the cost is O(V+E) however many test files there are - this is the batch
    question ("which files are reached at all"), not the attributed one.
    *forward_deps* maps a file to the files it depends on.

    Returns the reached set with the test files themselves removed: a test does
    not need a test.
    """
    if not test_files or max_depth < 1:
        return set()

    seen = set(test_files)
    frontier = set(test_files)
    for _ in range(max_depth):
        nxt: set[str] = set()
        for node in frontier:
            nxt |= forward_deps.get(node, frozenset())
        nxt -= seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    return seen - test_files


def forward_dependencies_from_graph(graph: Any) -> dict[str, set[str]]:
    """Build the file -> depends-on map from an in-memory NetworkX graph.

    Restricted to :data:`FILE_DEPENDENCY_EDGE_TYPES`, the codified answer to
    "which edges mean one file depends on another". Containment edges are the
    ones that matter to exclude: with ``defines`` in, a walk leaves the file
    layer into symbol nodes and every file defining a called symbol reads as a
    dependency of the caller's *file*.

    Returns an empty map for ``None`` - the health engine runs without a graph
    on some paths, and the documented outcome there is "no signal".
    """
    out: dict[str, set[str]] = {}
    if graph is None:
        return out
    try:
        edges = graph.edges(data=True)
    except Exception:
        return out
    for src, dst, data in edges:
        if (data or {}).get("edge_type") in FILE_DEPENDENCY_EDGE_TYPES:
            out.setdefault(src, set()).add(dst)
    return out


async def load_test_files(session: AsyncSession, repo_id: str) -> set[str]:
    """Paths of every file node ingestion classified as a test."""
    res = await session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repo_id,
            GraphNode.is_test == True,  # noqa: E712
        )
    )
    return {row[0] for row in res.all()}


async def tests_reaching(
    session: AsyncSession,
    repo_id: str,
    targets: list[str],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> dict[str, list[str]]:
    """Test files that reach each of *targets*, keyed by target path.

    The reverse of :func:`files_reached_by_tests`, and attributed: the walk
    runs backwards along dependency edges from the targets, carrying the seed
    each visited node came from, so the answer says *which* target each test
    guards rather than only that some test does.

    Attribution is why this is a separate walk rather than a filter over the
    forward one. It stays cheap because the seed set is a change's files, not
    the repository: one ``IN`` query per depth level, at most *max_depth* of
    them, which is the shape ``pr_blast._transitive_affected`` already uses.

    Targets nothing reaches are absent from the result. An empty dict is
    "no test reaches these"; the caller decides whether that reads as untested
    or as unknown.
    """
    seeds = sorted({t for t in targets if t})
    if not seeds or max_depth < 1:
        return {}

    test_files = await load_test_files(session, repo_id)
    if not test_files:
        return {}

    # node -> the seeds it was reached from. A node can serve several seeds (one
    # shared helper changed alongside its caller), and dropping that would
    # report the test against an arbitrary one of them.
    origins: dict[str, set[str]] = {s: {s} for s in seeds}
    found: dict[str, set[str]] = {}
    seed_set = set(seeds)
    frontier: deque[str] = deque(seeds)

    for _ in range(max_depth):
        level = list(frontier)
        frontier.clear()
        queued: set[str] = set()
        if not level:
            break
        for dependent, dependency in await _dependents_of(session, repo_id, level):
            carried = origins.get(dependency, frozenset())
            if not carried:
                continue
            if dependent in test_files:
                for seed in carried:
                    found.setdefault(seed, set()).add(dependent)
                # A test file is a leaf for this walk. Nothing imports a test,
                # and treating one as an intermediary would let "test A imports
                # test helper B" drag B's unrelated targets in.
                continue
            if dependent in seed_set:
                continue
            known = origins.setdefault(dependent, set())
            fresh = carried - known
            if not fresh:
                # Already reached carrying nothing new. Without this, a diamond
                # in the import graph re-walks the whole subtree once per path
                # into it.
                continue
            known |= fresh
            if dependent not in queued:
                queued.add(dependent)
                frontier.append(dependent)

    return {seed: sorted(tests)[:MAX_TESTS_PER_TARGET] for seed, tests in found.items() if tests}


async def _dependents_of(
    session: AsyncSession, repo_id: str, paths: list[str]
) -> list[tuple[str, str]]:
    """``(dependent, dependency)`` pairs for every file that depends on *paths*.

    Raw text SQL with an ``IN`` list, matching ``pr_blast._transitive_affected``:
    the edge table is the one place a per-level parameterised ``IN`` beats
    loading every row, and the two walks should not disagree about how they read
    it.
    """
    if not paths:
        return []
    dep_types = sorted(FILE_DEPENDENCY_EDGE_TYPES)
    ph = ",".join(f":p{i}" for i in range(len(paths)))
    et = ",".join(f":e{i}" for i in range(len(dep_types)))
    params: dict[str, Any] = {"repo_id": repo_id}
    params.update({f"p{i}": v for i, v in enumerate(paths)})
    params.update({f"e{i}": v for i, v in enumerate(dep_types)})
    rows = await session.execute(
        text(
            f"SELECT DISTINCT source_node_id, target_node_id FROM graph_edges "
            f"WHERE repository_id = :repo_id "
            f"AND target_node_id IN ({ph}) "
            f"AND edge_type IN ({et})"
        ),
        params,
    )
    return [(src, tgt) for src, tgt in rows]
