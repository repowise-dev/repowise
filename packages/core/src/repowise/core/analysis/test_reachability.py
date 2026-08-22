"""Static test-to-code map, derived from the dependency graph.

``test_coverage`` answers "which test executed this line" and is filled only by
a coverage report. Most repositories have none, so ``tests_to_run`` came back
empty, ``impacted_tests`` said "run the full suite", and ``untested_hotspot``
fell back to matching filenames.

Matching filenames fails both ways, and this repository is the proof. Of its six
worst bug-magnet files, five - ``call_resolver.py``, ``dead_code/analyzer.py``,
``pipeline/persist.py``, ``tool_answer/answer.py``, ``pr_blast.py`` - have no
file named for them anywhere under ``tests/`` and so read as untested, while the
graph names the test files that reach them. The sixth,
``analysis/health/engine.py``, is worse: the convention matches on basename
alone, so it paired with ``tests/unit/distill/test_engine.py`` - a different
engine, in a different subsystem - and called the file tested on the strength of
a name collision.

The graph already knows the answer. This module reads that relation. It is a
second signal beside the measured one, never a replacement and never averaged
with it.

Which graph, and why
--------------------
Two graphs can answer "does a test reach this file", and they are not equally
good at it.

The **call graph** is the primary signal: seed the symbols a test file declares,
walk ``EXECUTION_EDGE_TYPES`` forward, and every symbol reached is one the test
can actually run. This is the closer model of the question - the claim is about
execution, and these edges are execution.

The **import graph** is the broader, weaker tier: a test file that imports a
source file references it, which is real evidence and a much cruder one. A test
importing a module pulls in every symbol the module defines while the test body
may touch one function.

Measured against a real ``coverage run --contexts=test`` on this repository,
over the slice where per-test attribution is complete and both sides see the
same 37 test files (159 production files provably executed):

============================  =======  =======  =========  =======
forward signal                 claims  correct  precision   recall
============================  =======  =======  =========  =======
import graph, 1 hop                43       31      72.1%    19.5%
call graph, 3 hops                 48       44      91.7%    27.7%
call graph, 3 hops, filtered       46       44      95.7%    27.7%
both unioned                       57       45      78.9%    28.3%
============================  =======  =======  =========  =======

The call graph wins on both axes, so it leads. Unioning the import graph into it
buys 0.6 points of recall for 16.8 points of precision, and a false "something
reaches this" suppresses a real untested-hotspot finding, so the forward walk
does not union - it is call edges only.

The reverse walk combines them differently, because there the import tier can be
spent only on the targets the call graph said nothing about:

=============================  =======  ======  =========
reverse signal                 targets     hit  precision
=============================  =======  ======  =========
import graph, 1 hop                 32   96.9%      94.8%
call graph, 3 hops, filtered        46  100.0%      97.5%
both unioned                        47  100.0%      95.8%
call graph, else import graph       47  100.0%      97.5%
=============================  =======  ======  =========

Falling back is free where unioning is not: it answers one more target at
identical precision, because the second tier never speaks over the first.

Both tables compare the signals uncapped and over the same test set, which is
what makes them a choice between signals rather than two different experiments.
End to end through this module, with the walk seeing the repository's whole test
set and ``MAX_TESTS_PER_TARGET`` applied, the reverse walk scores 100.0% hit and
95.5% precision on the same slice; the gap is the cap truncating a repo-wide
list, not a difference in the walk.

Depth, and why 3
----------------
The call walk saturates at 3 hops - 3, 4 and 5 return the same 48 claims and 44
confirmations - so the recall ceiling is the call graph's own capture rate, not
the depth, and there is nothing to buy by walking further. The import walk keeps
its measured default of 1: a blanket second hop was tested and recovers 20 of
128 misses while adding 50 wrong claims, and both a facade-only second hop
through ``__init__.py`` (3 recovered, 5 added) and ``conftest.py`` transitivity
(zero recovered) were tested and disproved. What the import graph misses is
transitive *execution*, which import edges structurally cannot see, which is
what the call graph is for.

Why recall reads low, and why that is not the graph's fault
-----------------------------------------------------------
27.7% looks poor for a graph this dense, and the reason is what the ground truth
counts. ``coverage`` records a line as run whether a test called into it or
Python merely evaluated the module body on import, so a file that was only
imported is indistinguishable in the truth set from one a test drove. Splitting
the 159 truth files by how much of what ran was *inside a function body*:

=================================  =======  =======  ========
what actually ran in the file        files    found    recall
=================================  =======  =======  ========
nothing inside any function body        39        0        0%
under a quarter inside bodies           19        0        0%
a quarter to three quarters             45        3        7%
over three quarters inside bodies       56       43       77%
=================================  =======  =======  ========

A quarter of the "covered" files never ran a single line inside a function. The
walk is right to miss those: nothing called them, and claiming otherwise is the
import graph's error, which is what it costs 16.8 points of precision to make.

Of the 13 missed in the bottom row, 11 are ``alembic/versions/*`` migrations,
whose ``upgrade``/``downgrade`` the framework invokes by naming convention with
no static caller anywhere in the repository. Excluding those, the walk finds 43
of 45 files that a test genuinely exercised and that anything statically calls.
Chasing the headline number by widening the walk trades that away.

Confidence, and why one origin is dropped
-----------------------------------------
``graph_edges.resolution_origin`` records which strategy resolved a call edge,
and they are not equally trustworthy. ``global_unique`` binds a name to the only
symbol carrying it anywhere in the repository, which is a guess, and it is the
one origin the vocabulary itself scores at 0.50. Dropping it costs no recall at
all and buys 4.0 points of forward precision (91.7% -> 95.7%) and 1.1 of reverse
(96.4% -> 97.5%). Nothing else earns its keep: filtering ``receiver_global`` too
changes no number, and a confidence floor of 0.90 cuts recall from 27.7% to
23.3%.

What it claims, and what it does not
------------------------------------
Reaching is not executing. A call edge says control *can* flow, not that a given
test run did, so the inferred map **over-claims**, and the direction of the
error is known and one-sided:

* Sound as a floor. "Some test reaches this file, so do not call it untested"
  is safe: the edge is recorded, not guessed.
* Unsound as a quantity. No percentage may be derived from it. There is no line
  attribution here at all - reaching is a file-level fact - so a caller that
  needs "are these changed *lines* covered" must use the measured map or say it
  does not know.

Every surface that consumes this labels the result ``inferred``. Nothing here
writes to ``test_coverage``, and no row it produces is stored: the relation is a
bounded walk over ``graph_edges``/``graph_nodes``, which are already indexed and
already fresh. Materialising it would cost a transitive closure that goes stale
the moment the graph moves, to answer a query that is a breadth-first search
over data already in the database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.execution_graph import (
    UNRELIABLE_EXECUTION_ORIGINS,
    ExecutionGraphIndex,
    file_of_symbol,
)
from repowise.core.ingestion.models import EXECUTION_EDGE_TYPES, FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.persistence.models import GraphNode

# How many call hops a test may take and still be said to reach a file. The walk
# saturates here; see the Depth section above.
DEFAULT_CALL_DEPTH = 3

# How many import hops the weaker fallback tier may take. Still the measured
# choice; a blanket second hop costs more than it buys.
DEFAULT_MAX_DEPTH = 1

# Call edges whose resolver only matched a name. See the Confidence section.
UNRELIABLE_CALL_ORIGINS = UNRELIABLE_EXECUTION_ORIGINS

# Cap on how many test files one target reports. The consumers all cut their
# own lists shorter; the cap exists so a helper called from every test in the
# suite cannot produce an unbounded intermediate.
MAX_TESTS_PER_TARGET = 50

# Which tier answered. The CLI prints this so a reader can tell "this test runs
# into the file" from the weaker "this test imports it".
ReachedVia = Literal["call-graph", "import-graph"]

__all__ = [
    "DEFAULT_CALL_DEPTH",
    "DEFAULT_MAX_DEPTH",
    "MAX_TESTS_PER_TARGET",
    "UNRELIABLE_CALL_ORIGINS",
    "CallGraphView",
    "ReachedBy",
    "call_graph_from_db",
    "call_graph_from_graph",
    "files_reached_by_tests",
    "load_test_files",
    "tests_reaching",
    "tests_reaching_by_tier",
]


CallGraphView: TypeAlias = ExecutionGraphIndex


@dataclass(frozen=True)
class ReachedBy:
    """Tests reaching one target, and which tier found them.

    ``total`` is how many the walk actually found, before
    ``MAX_TESTS_PER_TARGET`` trimmed ``tests``. A surface that prints
    ``len(tests)`` as the answer is stating a cap as a measurement, and the cut
    is alphabetical, so *which* ones survive is arbitrary. Carry the total and
    say the bound.
    """

    tests: list[str]
    via: ReachedVia
    total: int = 0
    # Internal uncapped identities let downstream aggregators de-duplicate one
    # test that reaches several targets while public lists remain bounded.
    all_tests: tuple[str, ...] | None = None


def call_graph_from_graph(graph: Any) -> CallGraphView:
    """Build the call-reachability view from an in-memory NetworkX graph.

    ``defines`` alone bridges files to symbols: measured across this repository
    it reaches every symbol, class members included, so also walking
    ``has_method`` would add a second containment layer for nothing.

    Returns an empty view for ``None`` - the health engine runs without a graph
    on some paths, and the documented outcome there is "no signal".
    """
    return ExecutionGraphIndex(graph)


async def call_graph_from_db(session: AsyncSession, repo_id: str) -> CallGraphView:
    """The same view as :func:`call_graph_from_graph`, read from ``graph_edges``.

    The health engine holds the parsed graph in memory; a request handler holds
    a session. Both need the identical view, so this is a loader and not a
    second walk - :func:`files_reached_by_tests` still does the walking, and
    the two callers therefore cannot disagree about which files a test reaches.

    One bulk read rather than the reverse walk's per-level ``IN`` queries. That
    walk is seeded with a change's files; this question is seeded with the
    repository, and a per-level ``IN`` list over every file is the shape it was
    explicitly not built for.
    """
    wanted = ["defines", *sorted(EXECUTION_EDGE_TYPES)]
    params: dict[str, Any] = {"repo_id": repo_id}
    ets = _in_clause("e", wanted, params)
    rows = await session.execute(
        text(
            "SELECT source_node_id, target_node_id, edge_type, resolution_origin, "
            "call_lines_json "
            "FROM graph_edges WHERE repository_id = :repo_id "
            f"AND edge_type IN ({ets}) "
            "ORDER BY source_node_id, target_node_id, edge_type"
        ),
        params,
    )
    edge_rows = []
    for src, dst, edge_type, origin, call_lines_json in rows:
        try:
            call_lines = json.loads(call_lines_json or "[]")
        except (TypeError, ValueError):
            call_lines = []
        edge_rows.append((src, dst, edge_type, origin, call_lines))
    return ExecutionGraphIndex(edge_rows=edge_rows)


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
    call_depth: int = DEFAULT_CALL_DEPTH,
    import_depth: int = DEFAULT_MAX_DEPTH,
) -> dict[str, list[str]]:
    """Test files that reach each of *targets*, keyed by target path.

    Targets nothing reaches are absent from the result. An empty dict is
    "no test reaches these"; the caller decides whether that reads as untested
    or as unknown.
    """
    found = await tests_reaching_by_tier(
        session, repo_id, targets, call_depth=call_depth, import_depth=import_depth
    )
    return {target: reached.tests for target, reached in found.items()}


async def tests_reaching_by_tier(
    session: AsyncSession,
    repo_id: str,
    targets: list[str],
    *,
    call_depth: int = DEFAULT_CALL_DEPTH,
    import_depth: int = DEFAULT_MAX_DEPTH,
) -> dict[str, ReachedBy]:
    """:func:`tests_reaching`, also saying which tier answered each target.

    The call walk runs first; the import walk is then seeded with only the
    targets it left unanswered, so the weaker tier never speaks over the
    stronger one and costs nothing where the stronger one already spoke.

    Both walks are attributed: they run backwards from the targets carrying the
    seed each visited node came from, so the answer says *which* target each
    test guards rather than only that some test does. That is why these are
    separate walks rather than a filter over the forward one, and it stays cheap
    because the seed set is a change's files, not the repository - one ``IN``
    query per level, which is the shape ``pr_blast._transitive_affected``
    already uses.
    """
    seeds = sorted({t for t in targets if t})
    if not seeds:
        return {}

    test_files = await load_test_files(session, repo_id)
    if not test_files:
        return {}

    out: dict[str, ReachedBy] = {}
    if call_depth >= 1:
        found = await _call_reaching(session, repo_id, seeds, test_files, call_depth)
        for seed, tests in found.items():
            ordered = tuple(sorted(tests))
            out[seed] = ReachedBy(
                list(ordered[:MAX_TESTS_PER_TARGET]), "call-graph", len(ordered), ordered
            )

    unanswered = [seed for seed in seeds if seed not in out]
    if unanswered and import_depth >= 1:
        found = await _import_reaching(session, repo_id, unanswered, test_files, import_depth)
        for seed, tests in found.items():
            ordered = tuple(sorted(tests))
            out[seed] = ReachedBy(
                list(ordered[:MAX_TESTS_PER_TARGET]), "import-graph", len(ordered), ordered
            )
    return out


async def _call_reaching(
    session: AsyncSession,
    repo_id: str,
    seeds: list[str],
    test_files: set[str],
    max_depth: int,
) -> dict[str, set[str]]:
    """Tests that can execute into each seed file, walking call edges backwards.

    Starts one layer below the other walk: the seeds are files and call edges
    join symbols, so the first query resolves each seed to the symbols it
    declares, and the walk carries the seed from there.
    """
    declared = await _edges_from(session, repo_id, seeds, ["defines"])
    origins: dict[str, set[str]] = {}
    for seed, symbol in declared:
        origins.setdefault(symbol, set()).add(seed)
    if not origins:
        return {}

    found: dict[str, set[str]] = {}
    frontier = list(origins)
    for _ in range(max_depth):
        if not frontier:
            break
        level, frontier = frontier, []
        queued: set[str] = set()
        for caller, callee in await _edges_into(
            session,
            repo_id,
            level,
            sorted(EXECUTION_EDGE_TYPES),
            UNRELIABLE_CALL_ORIGINS,
        ):
            carried = origins.get(callee)
            if not carried:
                continue
            owner = file_of_symbol(caller)
            if owner in test_files:
                for seed in carried:
                    found.setdefault(seed, set()).add(owner)
                # A test is a leaf. Walking through one would let "test A calls
                # shared helper B" drag B's unrelated targets in.
                continue
            known = origins.setdefault(caller, set())
            fresh = carried - known
            if not fresh:
                continue
            known |= fresh
            if caller not in queued:
                queued.add(caller)
                frontier.append(caller)
    return found


async def _import_reaching(
    session: AsyncSession,
    repo_id: str,
    seeds: list[str],
    test_files: set[str],
    max_depth: int,
) -> dict[str, set[str]]:
    """Tests that import each seed file, directly or within *max_depth* hops."""
    origins: dict[str, set[str]] = {s: {s} for s in seeds}
    found: dict[str, set[str]] = {}
    seed_set = set(seeds)
    frontier = list(seeds)

    for _ in range(max_depth):
        if not frontier:
            break
        level, frontier = frontier, []
        queued: set[str] = set()
        for dependent, dependency in await _edges_into(
            session, repo_id, level, sorted(FILE_DEPENDENCY_EDGE_TYPES), frozenset()
        ):
            carried = origins.get(dependency)
            if not carried:
                continue
            if dependent in test_files:
                for seed in carried:
                    found.setdefault(seed, set()).add(dependent)
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
    return found


def _in_clause(prefix: str, values: list[str], params: dict[str, Any]) -> str:
    """Bind *values* as ``:prefix0, :prefix1, ...`` and return the clause body."""
    params.update({f"{prefix}{i}": v for i, v in enumerate(values)})
    return ",".join(f":{prefix}{i}" for i in range(len(values)))


async def _edges_from(
    session: AsyncSession, repo_id: str, sources: list[str], edge_types: list[str]
) -> list[tuple[str, str]]:
    """``(source, target)`` pairs leaving *sources* along *edge_types*."""
    if not sources:
        return []
    params: dict[str, Any] = {"repo_id": repo_id}
    src = _in_clause("s", sources, params)
    ets = _in_clause("e", edge_types, params)
    rows = await session.execute(
        text(
            "SELECT DISTINCT source_node_id, target_node_id FROM graph_edges "
            "WHERE repository_id = :repo_id "
            f"AND source_node_id IN ({src}) AND edge_type IN ({ets})"
        ),
        params,
    )
    return [(s, t) for s, t in rows]


async def _edges_into(
    session: AsyncSession,
    repo_id: str,
    targets: list[str],
    edge_types: list[str],
    excluded_origins: frozenset[str],
) -> list[tuple[str, str]]:
    """``(source, target)`` pairs arriving at *targets* along *edge_types*.

    Raw text SQL with an ``IN`` list, matching ``pr_blast._transitive_affected``:
    the edge table is the one place a per-level parameterised ``IN`` beats
    loading every row, and the two walks should not disagree about how they read
    it.
    """
    if not targets:
        return []
    params: dict[str, Any] = {"repo_id": repo_id}
    tgt = _in_clause("p", targets, params)
    ets = _in_clause("e", edge_types, params)
    origin_filter = ""
    if excluded_origins:
        # NULL means the row predates the vocabulary, not "unknown", so it has
        # to survive the filter, and a bare NOT IN would drop it.
        bad = _in_clause("o", sorted(excluded_origins), params)
        origin_filter = f" AND (resolution_origin IS NULL OR resolution_origin NOT IN ({bad}))"
    rows = await session.execute(
        text(
            "SELECT DISTINCT source_node_id, target_node_id FROM graph_edges "
            "WHERE repository_id = :repo_id "
            f"AND target_node_id IN ({tgt}) AND edge_type IN ({ets}){origin_filter}"
        ),
        params,
    )
    return [(s, t) for s, t in rows]
