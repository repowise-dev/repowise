"""What the test suite spends its time on: the slowest tests, and test time per covered file.

Reads stored JUnit timings (``evidence_test_timings``): the newest run of
each test-timing workload as ``evidence status`` picks it (a complete run
first), the newest environment when a workload ran in several. A test is charged once, at its
longest duration across those runs.

Test time per file is the summed duration of the timed tests that run it.
``measured`` when the per-test coverage map says which timed tests ran which
file (``test_coverage_sources``); ``inferred`` from each function's nearest tests
on the call graph (``function_facts.tests_json``) without it. Only production
files (:mod:`repowise.core.code_origin`) are listed; others are counted.
Timing orders tests and files, it never gates anything.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .status import run_status

#: Rows each list returns unless asked for another number.
DEFAULT_LIMIT = 20
#: What stores test timings, said when none are stored.
SETUP_COMMAND = "repowise evidence setup"


def _basis_runs(runs: Sequence[Any]) -> list[Any]:
    """Each test-timing workload's newest run, as ``evidence status`` picks it, then
    the newest of its environments.

    A complete run wins; a partly mapped one stands in for a workload with no
    other. *runs* are newest ingest first (``list_evidence_runs``).
    """
    from .hotness import _age_key, newest_runs

    timed = [r for r in runs if r.kind == "test_timing"]
    complete = [r for r in timed if not r.mapping_partial]
    covered = {(r.workload_name, r.environment) for r in complete}
    newest = newest_runs(complete) | newest_runs(
        r for r in timed if (r.workload_name, r.environment) not in covered
    )
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


async def _measured_costs(
    session: AsyncSession, repository_id: str, timings: dict[str, Any]
) -> dict[str, tuple[float, int, int]] | None:
    """``{file: (seconds, timed tests, tests)}`` from the per-test map; None without one,
    or when none of its test ids is a timed test's (an lcov ``TN:`` name, say)."""
    from ..persistence.crud.analysis.coverage_map import get_per_test_map, source_test_numbers

    tmap = await get_per_test_map(session, repository_id)
    if tmap is None:
        return None
    seconds = {
        no: timings[test_id].duration_s
        for no, (test_id, _) in enumerate(tmap.tests)
        if test_id in timings
    }
    if not seconds:
        return None
    out = {}
    for source, numbers in source_test_numbers(tmap).items():
        timed = [seconds[n] for n in numbers if n in seconds]
        out[source] = (sum(timed), len(timed), len(numbers))
    return out


async def _inferred_costs(
    session: AsyncSession, repository_id: str, timings: dict[str, Any], files: Iterable[str]
) -> dict[str, tuple[float, int, int]]:
    """``{file: (seconds, timed tests, tests)}`` from each function's nearest tests.

    A test selector is a test function's symbol id, or for JS and TS a test
    file; a test's parametrizations share its symbol, so their times add up.
    """
    from ..ingestion.symbol_identity import file_of_symbol
    from ..persistence.crud.analysis.function_facts import tests_in_files

    by_symbol: dict[str, float] = defaultdict(float)
    by_file: dict[str, float] = defaultdict(float)
    for row in timings.values():
        if row.symbol_id:
            by_symbol[row.symbol_id] += row.duration_s
        if row.test_file:
            by_file[row.test_file] += row.duration_s
    selectors: dict[str, set[str]] = defaultdict(set)
    for symbol, tests in (await tests_in_files(session, repository_id, files)).items():
        selectors[file_of_symbol(symbol)].update(t.selector for t in tests)
    out = {}
    for path, tests in selectors.items():
        timed = [by_symbol.get(s, by_file.get(s)) for s in tests]
        timed = [t for t in timed if t is not None]
        out[path] = (sum(timed), len(timed), len(tests))
    return out


async def _indexed_files(session: AsyncSession, repository_id: str) -> list[str]:
    from ..persistence.models import GraphNode

    result = await session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repository_id, GraphNode.node_type == "file"
        )
    )
    return list(result.scalars())


async def _file_costs(
    session: AsyncSession, repository_id: str, timings: dict[str, Any], limit: int
) -> dict[str, Any]:
    """Test time per production file, most first, with its basis."""
    from .ingest import load_origins

    costs = await _measured_costs(session, repository_id, timings)
    basis = "measured"
    if costs is None:
        basis = "inferred"
        files = await _indexed_files(session, repository_id)
        costs = await _inferred_costs(session, repository_id, timings, files)
    timed = {p: c for p, c in costs.items() if c[1]}
    origins = await load_origins(session, repository_id, timed)
    listed = sorted(
        (p for p in timed if origins[p] == "production"), key=lambda p: (-timed[p][0], p)
    )
    return {
        "basis": basis,
        "files": [
            {"file_path": p, "test_seconds": timed[p][0], "timed_tests": timed[p][1], "tests": timed[p][2]}
            for p in listed[:limit]
        ],
        "files_total": len(listed),
        # Test, tooling, generated and other non-production files run by timed tests.
        "not_production": len(timed) - len(listed),
    }


def no_timings(runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The :func:`suite_costs` answer with no test timing stored, and what writes them."""
    return {
        "status": "no_test_timings",
        "command": SETUP_COMMAND,
        "runs": runs or [],
        "slow_tests": [],
        "tests_total": 0,
        "file_costs": None,
    }


async def suite_costs(
    session: AsyncSession,
    repository_id: str,
    reference_commit: str | None,
    *,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """The slowest tests and the test time per covered file, JSON-ready.

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
    return {
        "status": "measured",
        "command": None,
        "runs": [run_status(r, reference_commit) for r in runs],
        "slow_tests": [_slow_test(r, workloads) for r in slow[:limit]],
        "tests_total": len(slow),
        "file_costs": await _file_costs(session, repository_id, timings, limit),
    }


__all__ = ["DEFAULT_LIMIT", "no_timings", "suite_costs"]
