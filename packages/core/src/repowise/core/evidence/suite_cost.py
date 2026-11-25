"""What the test suite spends its time on: the slowest tests, and the groups of tests
that make it slow.

Reads stored JUnit timings (``evidence_test_timings``): the newest run of
each test-timing workload as ``evidence status`` picks it (a complete run
first), the newest environment when a workload ran in several. A test is charged once, at its
longest duration across those runs.

Summed test time per production file does not say why a suite is slow: the files
the test setup imports lead every list with the whole suite's time. Excess does
(:func:`excess_clusters`): tests that run the same production code
(:mod:`repowise.core.code_origin`, from the per-test coverage map
``test_coverage_sources``) and take longer than the rest, with the fixture they
share when the index resolved one (:func:`_test_fixtures`). A suite under a
minute, or with no group a few seconds over the rest, gets one line and no list
(:func:`cost_fold`). Without a per-test map only the concentration is given.
Timing orders tests and groups, it never gates anything.

The ingest keeps a run's slowest tests under the row cap, and folds every test
of the run first (:func:`timing_summary`): seconds and tests per test file and
a duration histogram, so the suite's shape (:func:`suite_summary`) covers the
tests the cap left out.
"""

from __future__ import annotations

import heapq
import json
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analysis.health.coverage.contexts import PER_TEST_COVERAGE_COMMAND
from .status import run_status

#: Rows each list returns unless asked for another number.
DEFAULT_LIMIT = 20
#: What stores test timings, said when none are stored.
SETUP_COMMAND = "repowise evidence setup"
#: Upper bounds, in seconds, of the duration histogram's buckets: decades from
#: 1 ms, then one open bucket for anything slower than the last.
HISTOGRAM_BOUNDS = (0.001, 0.01, 0.1, 1.0, 10.0, 100.0)
#: A suite faster than this gets one line and no list: nothing in it is worth a
#: developer's time to speed up.
QUIET_SUITE_S = 60.0
#: Nor is a group of tests running less than this over the rest's pace.
MIN_EXCESS_S = 5.0
MAX_CLUSTERS = 3
#: Fewer tests than this running a file is a test of that file, not a group.
MIN_CLUSTER_TESTS = 3
#: A file more than this share of the timed tests run (one the test setup
#: imports) groups nothing.
MAX_REACH = 0.9
#: A group inside a larger one is reported instead of it when it carries this
#: share of the larger group's excess (:func:`_narrowest`).
NARROW_SHARE = 2 / 3
#: Files and slowest tests listed per cluster.
CLUSTER_FILES = 5
CLUSTER_TESTS = 3
#: A cluster names a fixture at least this share of its tests ask for...
FIXTURE_MIN_SHARE = 0.25
#: ...and that is asked for mostly by them (``tmp_path`` is everybody's).
FIXTURE_MIN_EXCLUSIVE = 0.5


def timing_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Every test row the join kept for one run (skipped entries are counted on
    the run, not here), folded before any cap: JSON-ready, stored on the run.

    ``tests`` counts the rows, ``timed`` those with a duration, ``seconds`` their
    sum. ``histogram`` holds ``[tests, seconds]`` per bucket of
    :data:`HISTOGRAM_BOUNDS` (a duration on a bound falls in that bound's
    bucket), the open bucket last. ``files`` maps each test file to
    ``[seconds, timed tests]``; a test with no file is in the totals only.
    """
    buckets = [[0, 0.0] for _ in range(len(HISTOGRAM_BOUNDS) + 1)]
    files: dict[str, list[Any]] = {}
    tests = timed = 0
    seconds = 0.0
    for row in rows:
        tests += 1
        duration = row.get("duration_s")
        if duration is None:
            continue
        timed += 1
        seconds += duration
        bucket = buckets[bisect_left(HISTOGRAM_BOUNDS, duration)]
        bucket[0] += 1
        bucket[1] += duration
        if row.get("test_file"):
            entry = files.setdefault(row["test_file"], [0.0, 0])
            entry[0] += duration
            entry[1] += 1
    return {
        "tests": tests,
        "timed": timed,
        "seconds": _rounded(seconds),
        "histogram": [[n, _rounded(total)] for n, total in buckets],
        "files": {path: [_rounded(total), n] for path, (total, n) in sorted(files.items())},
    }


def _rounded(seconds: float) -> float:
    # Microseconds: finer than any JUnit time, and keeps the stored JSON short.
    return round(seconds, 6)


def _fold(raw: str | None) -> dict[str, Any] | None:
    """A stored fold, or ``None`` when absent or not of this shape (another bucket set)."""
    try:
        fold = json.loads(raw) if raw else None
    except ValueError:
        return None
    if not isinstance(fold, dict) or len(fold.get("histogram") or ()) != len(HISTOGRAM_BOUNDS) + 1:
        return None
    return fold


def suite_summary(runs: Iterable[Any], limit: int = DEFAULT_LIMIT) -> dict[str, Any] | None:
    """The basis runs' stored :func:`timing_summary` folds added up, JSON-ready.

    ``test_files`` are the *limit* test files with the most seconds, most
    first; ``histogram`` names each bucket by its upper bound (``le``, null for
    the open bucket). ``None`` unless every run carries a fold: a run stored
    before the fold existed would leave the totals short without saying so.
    Ceiling: workloads that run the same test each count it, where the slow
    list charges a test once.
    """
    folds = [_fold(r.timing_summary_json) for r in runs]
    if not folds or any(f is None for f in folds):
        return None
    files: dict[str, list[Any]] = {}
    histogram = [[0, 0.0] for _ in range(len(HISTOGRAM_BOUNDS) + 1)]
    for fold in folds:
        for bucket, (n, total) in zip(histogram, fold["histogram"], strict=True):
            bucket[0] += n
            bucket[1] += total
        for path, (total, n) in fold["files"].items():
            entry = files.setdefault(path, [0.0, 0])
            entry[0] += total
            entry[1] += n
    ranked = sorted(files.items(), key=lambda item: (-item[1][0], item[0]))
    bounds: list[float | None] = [*HISTOGRAM_BOUNDS, None]
    return {
        "tests": sum(f["tests"] for f in folds),
        "timed": sum(f["timed"] for f in folds),
        "seconds": _rounded(sum(f["seconds"] for f in folds)),
        "histogram": [
            {"le": le, "tests": n, "seconds": _rounded(total)}
            for le, (n, total) in zip(bounds, histogram, strict=True)
        ],
        "test_files": [
            {"test_file": path, "seconds": _rounded(total), "tests": n}
            for path, (total, n) in ranked[:limit]
        ],
        "test_files_total": len(files),
    }


def _basis_runs(runs: Sequence[Any]) -> list[Any]:
    """Each test-timing workload's newest run, as ``evidence status`` picks it, then
    the newest of its environments.

    A complete run wins; a partly mapped one stands in for a workload with no
    other. *runs* are newest ingest first (``list_evidence_runs``).
    """
    from .hotness import _age_key, newest_runs

    timed = [r for r in runs if r.kind == "test_timing"]
    complete = [r for r in timed if not r.mapping_partial]
    # Keyed by workload alone: a partial run of one environment never displaces
    # the complete run of another.
    covered = {r.workload_name for r in complete}
    newest = newest_runs(complete) | newest_runs(r for r in timed if r.workload_name not in covered)
    best: dict[str, Any] = {}
    for run in timed:
        if run.id in newest and (
            run.workload_name not in best or _age_key(run) > _age_key(best[run.workload_name])
        ):
            best[run.workload_name] = run
    return sorted(best.values(), key=_age_key, reverse=True)


async def _timings(session: AsyncSession, run_ids: list[str]) -> dict[str, Any]:
    """Each test's row with its longest duration across *run_ids*, by test id."""
    from ..persistence.models import EvidenceTestTiming

    rows = (
        await session.execute(
            select(EvidenceTestTiming).where(
                EvidenceTestTiming.run_id.in_(run_ids),
                EvidenceTestTiming.duration_s.is_not(None),
            )
        )
    ).scalars()
    out: dict[str, Any] = {}
    for row in rows:
        kept = out.get(row.test_id)
        if kept is None or row.duration_s > kept.duration_s:
            out[row.test_id] = row
    return out


def _slow_test(row: Any, workloads: dict[str, str]) -> dict[str, Any]:
    return {
        "test_id": row.test_id,
        "test_file": row.test_file,
        "duration_s": row.duration_s,
        "outcome": row.outcome,
        # Its time may include fixtures its whole class or module shares.
        "first_in_class": row.first_in_class,
        "workload": workloads[row.run_id],
    }


class Fixture(NamedTuple):
    """A fixture a test asks for by name, where it is defined."""

    name: str
    file_path: str
    line: int | None


def concentration(durations: Iterable[float], tests: int, seconds: float) -> dict[str, Any] | None:
    """How much of *seconds* the slowest tenth of *tests* take; ``None`` for an empty suite.

    *durations* may be the slowest tests only (the ingest's row cap keeps
    those); *tests* and *seconds* are the whole suite's.
    """
    if tests <= 0 or seconds <= 0:
        return None
    # A row cap under a tenth of the suite leaves fewer durations: name only those.
    slowest = heapq.nlargest(max(1, tests // 10), durations)
    if not slowest:
        return None
    return {"tests": len(slowest), "share": round(min(1.0, sum(slowest) / seconds), 4)}


def excess_clusters(
    seconds: Mapping[str, float],
    reach: Mapping[str, Collection[str]],
    suite_seconds: float,
    fixtures: Mapping[str, Collection[Fixture]] | None = None,
    *,
    unseen: tuple[int, float] = (0, 0.0),
) -> list[dict[str, Any]]:
    """Up to :data:`MAX_CLUSTERS` groups of tests that run the same production code
    and take longer than the rest, most excess first.

    *seconds* maps each timed test to its duration, *reach* each test to the
    production files it runs (the per-test map); only tests in both, running
    some production file, count. A file's excess is ``n x (mean of its n
    tests - mean of the others)``, over files at least :data:`MIN_CLUSTER_TESTS`
    tests and at most :data:`MAX_REACH` of the tests still ranked run: a file nearly every
    test runs (one the test setup imports) separates nothing. The file with the
    most excess and every file run by exactly the same tests form a cluster.
    Its tests leave the pool before the next cluster is ranked, so clusters
    never share a test and the store files one fixture opens are one cluster,
    not four. Ranking stops below :data:`MIN_EXCESS_S`.

    *fixtures* maps every timed test to the fixtures it asks for; a cluster
    names the one most of its tests use and few other timed tests do
    (:func:`_shared_fixture`). ``share`` is of *suite_seconds*. *unseen* is the
    count and seconds of timed tests *seconds* lacks (the ingest's row cap keeps
    the slowest): they count among the others, never in a cluster.
    """
    pool = {t: seconds[t] for t in sorted(reach) if reach[t] and t in seconds}
    fixtures = fixtures or {}
    users = Counter(f for fs in fixtures.values() for f in set(fs))
    clusters: list[dict[str, Any]] = []
    while len(clusters) < MAX_CLUSTERS:
        by_file: dict[str, list[str]] = defaultdict(list)
        for test in pool:
            for path in reach[test]:
                by_file[path].append(test)
        total = sum(pool.values()) + unseen[1]
        size = len(pool) + unseen[0]
        ranked = _ranked_files(by_file, pool, total, size)
        if not ranked or ranked[0][0] < MIN_EXCESS_S:
            break
        excess, _, members = _narrowest(ranked)
        inside = sum(pool[t] for t in members)
        slowest = sorted(members, key=lambda t: (-pool[t], t))[:CLUSTER_TESTS]
        outer = ranked[0][2]
        clusters.append(
            {
                **_within(_run_by(by_file, members), len(members)),
                "mean_s": _rounded(inside / len(members)),
                "others_mean_s": _rounded((total - inside) / (size - len(members))),
                "excess_s": _rounded(excess),
                "share": round(excess / suite_seconds, 4) if suite_seconds > 0 else None,
                "slowest": [{"test_id": t, "duration_s": pool[t]} for t in slowest],
                "fixture": _shared_fixture(members, fixtures, users),
                # The larger group it was narrowed from: code all of its tests run too.
                "within": None if outer == members else _within(_run_by(by_file, outer), len(outer)),
            }
        )
        for test in members:
            del pool[test]
    return clusters


def _ranked_files(
    by_file: Mapping[str, list[str]], pool: Mapping[str, float], total: float, size: int
) -> list[tuple[float, str, frozenset[str]]]:
    """``(excess, file, its tests)`` per file, most excess first; *total* and *size*
    are the seconds and tests still ranked, the ones the row cap cut included."""
    ranked = []
    for path, tests in by_file.items():
        n = len(tests)
        # Of the tests still ranked: a file nearly all of them run separates nothing.
        if n < MIN_CLUSTER_TESTS or n > MAX_REACH * size or n == size:
            continue
        inside = sum(pool[t] for t in tests)
        ranked.append((inside - n * (total - inside) / (size - n), path, frozenset(tests)))
    ranked.sort(key=lambda c: (-c[0], c[1]))
    return ranked


def _run_by(by_file: Mapping[str, Collection[str]], group: frozenset[str]) -> list[str]:
    """The files exactly *group*'s tests run."""
    return sorted(p for p, ts in by_file.items() if len(ts) == len(group) and group.issuperset(ts))


def _within(files: list[str], tests: int) -> dict[str, Any]:
    return {"files": files[:CLUSTER_FILES], "files_total": len(files), "tests": tests}


def _narrowest(
    ranked: list[tuple[float, str, frozenset[str]]],
) -> tuple[float, str, frozenset[str]]:
    """The top candidate, or the narrowest group inside it that still carries
    :data:`NARROW_SHARE` of its excess. When every store test also opens the
    plugin registry, the registry's group holds the store's and most of its
    excess is theirs: the store is the answer, and the registry's remainder is
    ranked again once the store's tests leave the pool. *ranked* is most excess
    first; the share is of the top's excess, so narrowing never drifts to a
    handful of tests carrying a fraction of it."""
    top = best = ranked[0]
    while True:
        inner = next((c for c in ranked if c[0] >= NARROW_SHARE * top[0] and c[2] < best[2]), None)
        if inner is None:
            return best
        best = inner


def _shared_fixture(
    members: Collection[str], fixtures: Mapping[str, Collection[Fixture]], users: Counter[Fixture]
) -> dict[str, Any] | None:
    """The fixture the most *members* ask for, when at least :data:`FIXTURE_MIN_SHARE`
    of them do and at least :data:`FIXTURE_MIN_EXCLUSIVE` of the timed tests asking
    for it are members: a fixture every kind of test uses (``tmp_path``) says
    nothing of this group. ``tests`` counts the members asking for it,
    ``others`` the other timed tests that do."""
    inside = Counter(f for t in members for f in set(fixtures.get(t, ())))
    floor = max(2, FIXTURE_MIN_SHARE * len(members))
    picked = [f for f, n in inside.items() if n >= floor and n >= FIXTURE_MIN_EXCLUSIVE * users[f]]
    if not picked:
        return None
    fixture = min(picked, key=lambda f: (-inside[f], users[f], f.name, f.file_path, f.line or 0))
    return {
        **fixture._asdict(),
        "tests": inside[fixture],
        "others": users[fixture] - inside[fixture],
    }


def cost_fold(
    seconds: Mapping[str, float],
    reach: Mapping[str, Collection[str]] | None,
    fixtures: Mapping[str, Collection[Fixture]] | None = None,
    *,
    timed: int | None = None,
    suite_seconds: float | None = None,
) -> dict[str, Any]:
    """What the suite spends its time on, JSON-ready: the silence verdict, how
    concentrated the time is, and the :func:`excess_clusters`.

    *seconds* are the timed tests' durations, *reach* the production files each
    runs (``None`` without a per-test map), *fixtures* what each asks for.
    *timed* and *suite_seconds* are the whole suite's when *seconds* holds only
    the slowest tests (the row cap); they default to *seconds*' own.

    ``verdict``: ``small_suite`` under :data:`QUIET_SUITE_S`; ``no_test_map``
    without a per-test map (``command`` records one); ``small_excess`` when no
    group of tests runs :data:`MIN_EXCESS_S` over the rest; else ``clusters``.
    Only ``clusters`` lists anything.
    """
    timed = len(seconds) if timed is None else timed
    total = sum(seconds.values()) if suite_seconds is None else suite_seconds
    clusters: list[dict[str, Any]] = []
    if total < QUIET_SUITE_S:
        verdict = "small_suite"
    elif reach is None:
        verdict = "no_test_map"
    else:
        # Tests the row cap cut are the fastest: they run at the others' pace.
        unseen = (max(0, timed - len(seconds)), max(0.0, total - sum(seconds.values())))
        clusters = excess_clusters(seconds, reach, total, fixtures, unseen=unseen)
        verdict = "clusters" if clusters else "small_excess"
    return {
        "verdict": verdict,
        "seconds": _rounded(total),
        "timed": timed,
        "concentration": concentration(seconds.values(), timed, total),
        "clusters": clusters,
        "command": PER_TEST_COVERAGE_COMMAND if verdict == "no_test_map" else None,
    }


async def _test_reach(
    session: AsyncSession, repository_id: str, timings: Mapping[str, Any]
) -> dict[str, set[str]] | None:
    """The production files each timed test runs, from the per-test map; ``None``
    without a map, or when none of its test ids is a timed test's (an lcov ``TN:``
    name, say). File origin is :mod:`repowise.core.code_origin`'s."""
    from ..persistence.crud.analysis.coverage_map import get_per_test_map, source_test_numbers
    from .ingest import load_origins

    tmap = await get_per_test_map(session, repository_id)
    if tmap is None:
        return None
    timed = {no: test_id for no, (test_id, _) in enumerate(tmap.tests) if test_id in timings}
    if not timed:
        return None
    sources = source_test_numbers(tmap)
    origins = await load_origins(session, repository_id, sources)
    reach: dict[str, set[str]] = {test_id: set() for test_id in timed.values()}
    for source, numbers in sources.items():
        if origins[source] != "production":
            continue
        for no in numbers:
            if no in timed:
                reach[timed[no]].add(source)
    return reach


async def _test_fixtures(
    session: AsyncSession, repository_id: str, timings: Mapping[str, Any]
) -> dict[str, frozenset[Fixture]]:
    """The fixtures each timed test asks for, as the index resolved them.

    Ingestion links a test function to each fixture it requests by name (its
    parameters, ``usefixtures``) with a ``framework_binds`` edge stamped
    ``pytest_edges.FIXTURE_HINT``, resolved in pytest's own scope order; this
    reads those edges. Direct requests only: a fixture that another fixture
    asks for is not counted for the test. Parametrizations share their
    function's fixtures.
    """
    from ..ingestion.framework_edges.pytest_edges import FIXTURE_HINT
    from ..ingestion.symbol_identity import file_of_symbol
    from ..persistence.models import GraphEdge, GraphNode

    symbols = sorted({row.symbol_id for row in timings.values() if row.symbol_id})
    asks: dict[str, set[str]] = defaultdict(set)
    for i in range(0, len(symbols), 400):
        result = await session.execute(
            select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
                GraphEdge.repository_id == repository_id,
                GraphEdge.edge_type == "framework_binds",
                GraphEdge.hint_source == FIXTURE_HINT,
                GraphEdge.source_node_id.in_(symbols[i : i + 400]),
            )
        )
        for source, target in result:
            asks[source].add(target)
    targets = sorted({t for ts in asks.values() for t in ts})
    found: dict[str, Fixture] = {}
    for i in range(0, len(targets), 400):
        result = await session.execute(
            select(
                GraphNode.node_id, GraphNode.name, GraphNode.file_path, GraphNode.start_line
            ).where(
                GraphNode.repository_id == repository_id,
                GraphNode.node_id.in_(targets[i : i + 400]),
            )
        )
        for node_id, name, path, line in result:
            found[node_id] = Fixture(
                name or node_id.rsplit("::", 1)[-1], path or file_of_symbol(node_id), line
            )
    return {
        test_id: frozenset(found[t] for t in asks[row.symbol_id] if t in found)
        for test_id, row in timings.items()
        if row.symbol_id in asks
    }


def no_timings(runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The :func:`suite_costs` answer with no test timing stored, and what writes them."""
    return {
        "status": "no_test_timings",
        "command": SETUP_COMMAND,
        "runs": runs or [],
        "slow_tests": [],
        "tests_total": 0,
        "cost": None,
        "suite": None,
    }


async def suite_costs(
    session: AsyncSession,
    repository_id: str,
    reference_commit: str | None,
    *,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """The slowest tests and where the suite's time goes (:func:`cost_fold`), JSON-ready.

    *reference_commit* is the indexed commit each basis run's freshness is
    judged against. With no timing stored, ``status`` is ``no_test_timings``
    and ``command`` says what writes them.
    """
    from ..persistence.crud import list_evidence_runs

    runs = _basis_runs(await list_evidence_runs(session, repository_id))
    timings = await _timings(session, [r.id for r in runs]) if runs else {}
    if not timings:
        return no_timings([run_status(r, reference_commit) for r in runs])
    workloads = {r.id: r.workload_name for r in runs}
    slow = sorted(timings.values(), key=lambda r: (-r.duration_s, r.test_id))
    suite = suite_summary(runs, limit)
    seconds = {t: row.duration_s for t, row in timings.items()}
    # One run's fold covers the tests its row cap left out (the stored rows are the
    # slowest). Folds of several runs add up a test each workload ran, where
    # *seconds* charges it once, so they would inflate the suite. Ceiling: with
    # several capped workloads the cut tests are not counted.
    timed = suite["timed"] if suite and len(runs) == 1 else len(seconds)
    total = suite["seconds"] if suite and len(runs) == 1 else sum(seconds.values())
    reach = fixtures = None
    if total >= QUIET_SUITE_S:
        reach = await _test_reach(session, repository_id, timings)
        if reach is not None:
            fixtures = await _test_fixtures(session, repository_id, timings)
    return {
        "status": "measured",
        "command": None,
        "runs": [run_status(r, reference_commit) for r in runs],
        "slow_tests": [_slow_test(r, workloads) for r in slow[:limit]],
        "tests_total": len(slow),
        "cost": cost_fold(seconds, reach, fixtures, timed=timed, suite_seconds=total),
        "suite": suite,
    }


__all__ = [
    "DEFAULT_LIMIT",
    "HISTOGRAM_BOUNDS",
    "Fixture",
    "concentration",
    "cost_fold",
    "excess_clusters",
    "no_timings",
    "suite_costs",
    "suite_summary",
    "timing_summary",
]
