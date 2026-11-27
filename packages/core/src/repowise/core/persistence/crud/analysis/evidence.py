"""CRUD operations for the runtime evidence store (``evidence_*`` tables).

One ingest writes one ``evidence_runs`` row and that run's rows of its kind,
in bulk. Pruning deletes whole runs by id, one statement per table.

``symbol_hotness`` and ``workload_hotness`` are replaced whole per
repository (one delete, bulk inserts) by ``repowise.core.evidence.hotness``;
:func:`hotness_for_symbols` (per function) and :func:`ranked_by_workload`
(per workload) are the readers every surface goes through.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC
from itertools import zip_longest
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Table, delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....evidence.kinds import STORE_TABLES, evidence_kinds
from ...models import (
    Base,
    EvidenceBenchmarkResult,
    EvidenceRouteLatency,
    EvidenceRun,
    EvidenceStatementStats,
    EvidenceSymbolCost,
    SymbolHotness,
    WorkloadHotness,
    _new_uuid,
    _now_utc,
)
from .._shared import _BATCH_SIZE

if TYPE_CHECKING:
    from ....evidence.hotness import CostFacts, RunFacts
    from ....evidence.measured_fold import RouteRun, StatementRun


def evidence_row_table(kind: str) -> Table | None:
    """The table that holds a run's rows, by the run's evidence kind (``evidence.kinds``)."""
    spec = evidence_kinds.get(kind)
    return None if spec is None else Base.metadata.tables[spec.table]


#: :func:`evidence_row_table` of every kind registered at import (the built-in ones).
EVIDENCE_ROW_TABLES: dict[str, Table] = {
    name: Base.metadata.tables[spec.table] for name, spec in evidence_kinds.items()
}


@dataclass(frozen=True)
class EvidenceWrite:
    """What :func:`save_evidence_run` did."""

    run_id: str
    #: The same content was already stored at this commit; only its time moved.
    restamped: bool
    rows_written: int
    runs_pruned: int


async def save_evidence_run(
    session: AsyncSession,
    repository_id: str,
    run: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    *,
    history_runs: int,
    max_runs: int,
) -> EvidenceWrite:
    """Store one run and its rows, then prune the history of its kind.

    *run* holds ``EvidenceRun`` columns (``kind`` and ``run_key`` required);
    *rows* hold the columns of the kind's table, without ``run_id`` and
    ``repository_id``, one row per key: a writer folds repeats (reruns, a
    benchmark reported per count) before calling, and a repeat raises before
    anything is written. A run whose ``run_key`` and commit match a stored
    run is one measurement ingested twice: the stored run is restamped and
    nothing else is written, like ``coverage._record_ingest``, so ``run_key``
    must cover everything the run row records. A run with no commit (a
    production window whose deployed commit is unknown) matches only a stored
    run with no commit, by ``run_key`` alone: the same file added again, by a
    nightly job, is one measurement. Pruning keeps
    *history_runs* per workload and *max_runs* per kind, never dropping a
    workload's newest run (:func:`prune_evidence_runs`).
    """
    kind = run["kind"]
    table = evidence_row_table(kind)
    if table is None:
        raise ValueError(f"unknown evidence kind {kind!r}")
    if history_runs < 1 or max_runs < 1:
        raise ValueError("history_runs and max_runs must keep at least the new run")
    run_id = _new_uuid()
    filled = [
        _full_row(table, {**row, "run_id": run_id, "repository_id": repository_id})
        for row in rows
    ]
    _refuse_repeated_keys(table, filled)
    at = run.get("ingested_at") or _now_utc()
    commit = run.get("commit_sha")
    same = (
        await session.execute(
            select(EvidenceRun)
            .where(
                EvidenceRun.repository_id == repository_id,
                EvidenceRun.run_key == run["run_key"],
                EvidenceRun.commit_sha.is_(None)
                if commit is None
                else EvidenceRun.commit_sha == commit,
            )
            .order_by(EvidenceRun.ingested_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if same is not None:
        same.ingested_at = at
        # A sender that says when the run was measured corrects the time (a source re-fetch).
        if run.get("measured_at") is not None:
            same.measured_at = run["measured_at"]
        # A run stored before the fold existed gets it from the same file.
        if same.timing_summary_json is None and run.get("timing_summary_json"):
            same.timing_summary_json = run["timing_summary_json"]
        await session.flush()
        return EvidenceWrite(same.id, restamped=True, rows_written=0, runs_pruned=0)

    # One savepoint, so a failed row batch leaves no run without its rows.
    async with session.begin_nested():
        await session.execute(
            insert(EvidenceRun.__table__),
            [
                _full_row(
                    EvidenceRun.__table__,
                    {**run, "id": run_id, "ingested_at": at, "repository_id": repository_id},
                )
            ],
        )
        # On the table, not the mapper: the ORM bulk path splits a batch
        # wherever a column flips between NULL and a value.
        for i in range(0, len(filled), _BATCH_SIZE):
            await session.execute(insert(table), filled[i : i + _BATCH_SIZE])
    pruned = await prune_evidence_runs(
        session, repository_id, kind, per_workload=history_runs, per_kind=max_runs
    )
    return EvidenceWrite(run_id, restamped=False, rows_written=len(filled), runs_pruned=pruned)


async def list_evidence_runs(session: AsyncSession, repository_id: str) -> list[EvidenceRun]:
    """Every stored run of the repository, newest first (bounded by the prune caps)."""
    result = await session.execute(
        select(EvidenceRun)
        .where(EvidenceRun.repository_id == repository_id)
        .order_by(EvidenceRun.ingested_at.desc(), EvidenceRun.id.desc())
    )
    return list(result.scalars())


async def newest_evidence_run(
    session: AsyncSession, repository_id: str, *, prefer_commit: str | None = None
) -> tuple[int, Any]:
    """How many runs are stored, and the newest one's ``commit_sha`` and
    ``ingested_at`` (``None`` with no run). Two keyed reads, no row bodies.

    With *prefer_commit*, a run at that commit wins over a newer ingest of
    another: re-adding an old profile restamps its ingest time, and must not
    make current evidence read stale.
    """
    count = (
        await session.execute(
            select(func.count(EvidenceRun.id)).where(EvidenceRun.repository_id == repository_id)
        )
    ).scalar_one()
    if not count:
        return 0, None
    preferred = (EvidenceRun.commit_sha == prefer_commit,) if prefer_commit else ()
    newest = (
        await session.execute(
            select(EvidenceRun.commit_sha, EvidenceRun.ingested_at)
            .where(EvidenceRun.repository_id == repository_id)
            .order_by(
                *(c.desc() for c in preferred), EvidenceRun.ingested_at.desc(), EvidenceRun.id.desc()
            )
            .limit(1)
        )
    ).one()
    return count, newest


async def prune_evidence_runs(
    session: AsyncSession,
    repository_id: str,
    kind: str,
    *,
    per_workload: int,
    per_kind: int,
) -> int:
    """Drop runs of *kind*, oldest first, past the newest *per_workload* of
    their workload (name and environment), then past *per_kind* overall.

    The newest run of every workload always stays, so one busy workload
    cannot evict another's history. The kind so holds at most
    ``max(per_kind, number of workloads)`` runs: past *per_kind* workloads,
    each keeps only its newest. Returns how many runs were dropped."""
    runs = (
        await session.execute(
            select(EvidenceRun.id, EvidenceRun.workload_name, EvidenceRun.environment)
            .where(EvidenceRun.repository_id == repository_id, EvidenceRun.kind == kind)
            .order_by(EvidenceRun.ingested_at.desc(), EvidenceRun.id.desc())
        )
    ).all()
    seen: dict[tuple[str, str], int] = {}
    newest: list[str] = []
    older: list[str] = []
    stale: list[str] = []
    for run_id, workload, environment in runs:
        series = (workload, environment)
        seen[series] = seen.get(series, 0) + 1
        if seen[series] == 1:
            newest.append(run_id)
        elif seen[series] <= per_workload:
            older.append(run_id)
        else:
            stale.append(run_id)
    # ``older`` is newest first, so the cap keeps the most recent of them.
    stale.extend(older[max(per_kind - len(newest), 0) :])
    await delete_evidence_runs(session, stale)
    return len(stale)


async def delete_evidence_runs(session: AsyncSession, run_ids: list[str]) -> None:
    """Delete *run_ids* and their rows: one statement per table and chunk.

    The rows are deleted explicitly rather than left to ``ON DELETE CASCADE``,
    so a connection opened without SQLite's foreign-key pragma leaves no
    orphans either.
    """
    for i in range(0, len(run_ids), _BATCH_SIZE):
        chunk = run_ids[i : i + _BATCH_SIZE]
        for table in (Base.metadata.tables[name] for name in STORE_TABLES):
            await session.execute(delete(table).where(table.c.run_id.in_(chunk)))
        await session.execute(delete(EvidenceRun).where(EvidenceRun.id.in_(chunk)))


async def benchmark_results(
    session: AsyncSession, run_ids: list[str]
) -> list[EvidenceBenchmarkResult]:
    """The benchmark rows of *run_ids*, one statement per chunk."""
    out: list[EvidenceBenchmarkResult] = []
    for i in range(0, len(run_ids), _BATCH_SIZE):
        result = await session.execute(
            select(EvidenceBenchmarkResult).where(
                EvidenceBenchmarkResult.run_id.in_(run_ids[i : i + _BATCH_SIZE])
            )
        )
        out.extend(result.scalars())
    return out


async def benchmark_runs(session: AsyncSession, repository_id: str) -> list[EvidenceRun]:
    """The repository's ``benchmark`` kind runs (results, not profiles)."""
    result = await session.execute(
        select(EvidenceRun).where(
            EvidenceRun.repository_id == repository_id, EvidenceRun.kind == "benchmark"
        )
    )
    return list(result.scalars())


async def benchmark_callees(
    session: AsyncSession, repository_id: str, benchmarks: Iterable[str | None]
) -> dict[str, list[str]]:
    """The production functions each of *benchmarks* calls directly, by
    benchmark symbol id: graph ``calls`` edges whose target sits in a file
    whose ``code_origin`` is ``production``. One statement per chunk."""
    from ....evidence.ingest import load_origins
    from ....ingestion.symbol_identity import file_of_symbol
    from ...models import GraphEdge

    wanted = sorted({b for b in benchmarks if b})
    out: dict[str, list[str]] = {}
    for i in range(0, len(wanted), _BATCH_SIZE):
        result = await session.execute(
            select(GraphEdge.source_node_id, GraphEdge.target_node_id)
            .where(
                GraphEdge.repository_id == repository_id,
                GraphEdge.source_node_id.in_(wanted[i : i + _BATCH_SIZE]),
                GraphEdge.edge_type == "calls",
            )
            .order_by(GraphEdge.source_node_id, GraphEdge.target_node_id)
        )
        for source, target in result:
            out.setdefault(source, []).append(target)
    origins = await load_origins(
        session, repository_id, {file_of_symbol(t) for found in out.values() for t in found}
    )
    return {
        source: kept
        for source, found in out.items()
        if (kept := [t for t in found if origins.get(file_of_symbol(t)) == "production"])
    }


async def hotness_inputs(
    session: AsyncSession, repository_id: str
) -> tuple[list[RunFacts], dict[str, list[CostFacts]]] | None:
    """The profile runs a hotness rebuild reads, and their cost rows by run id.

    ``None`` when the repository stores no profile run, after one indexed
    lookup: the caller then has nothing to rebuild. Mostly unmapped runs are
    stored but are no basis, so they are left out. A run's age is when it
    was measured, else when it was ingested.
    """
    from ....evidence.hotness import CostFacts, RunFacts

    is_profile = (EvidenceRun.repository_id == repository_id, EvidenceRun.kind == "profile")
    if (await session.execute(select(EvidenceRun.id).where(*is_profile).limit(1))).first() is None:
        return None
    runs = [
        RunFacts(
            id=run.id,
            workload_name=run.workload_name,
            workload_class=run.workload_class,
            value_kind=run.value_kind,
            unit=run.unit,
            commit_sha=run.commit_sha,
            measured_at=run.measured_at or run.ingested_at,
            total_value=run.total_value,
            explained_value=run.explained_value,
            total_samples=run.total_samples,
            unlisted_symbols=run.unlisted_symbols,
            repo_self_value=run.repo_self_value,
            wait_value=run.wait_value,
            library_value=run.library_value,
            environment=run.environment,
        )
        for run in (
            await session.execute(
                select(EvidenceRun).where(*is_profile, EvidenceRun.mapping_partial.is_(False))
            )
        ).scalars()
    ]
    costs: dict[str, list[CostFacts]] = {run.id: [] for run in runs}
    cost = EvidenceSymbolCost
    ids = list(costs)
    for i in range(0, len(ids), _BATCH_SIZE):
        result = await session.execute(
            select(
                cost.run_id,
                cost.symbol_id,
                cost.lineage_id,
                cost.body_digest,
                cost.total_value,
                cost.self_value,
                cost.total_samples,
                cost.call_count,
                cost.self_samples,
                cost.inside_json,
                cost.drivers_json,
            ).where(cost.run_id.in_(ids[i : i + _BATCH_SIZE]))
        )
        for row in result:
            costs[row.run_id].append(CostFacts(*row[1:]))
    return runs, costs


async def replace_symbol_hotness(
    session: AsyncSession, repository_id: str, rows: Iterable[Mapping[str, Any]]
) -> int:
    """Replace the repository's ``symbol_hotness`` with *rows*: one delete, bulk inserts.

    *rows* hold the table's columns without ``repository_id`` and
    ``built_at``, one per symbol id. Returns the rows written.
    """
    at = _now_utc()
    return await _replace(session, SymbolHotness, repository_id, [{**r, "built_at": at} for r in rows])


async def replace_workload_hotness(
    session: AsyncSession, repository_id: str, rows: Iterable[Mapping[str, Any]]
) -> int:
    """Replace the repository's ``workload_hotness`` with *rows*: one delete, bulk inserts.

    *rows* hold the table's columns without ``repository_id``, one per (run,
    symbol). Returns the rows written.
    """
    return await _replace(session, WorkloadHotness, repository_id, rows)


async def _replace(
    session: AsyncSession, model: Any, repository_id: str, rows: Iterable[Mapping[str, Any]]
) -> int:
    table = cast(Table, model.__table__)
    filled = [_full_row(table, {**row, "repository_id": repository_id}) for row in rows]
    _refuse_repeated_keys(table, filled)
    await session.execute(delete(model).where(model.repository_id == repository_id))
    for i in range(0, len(filled), _BATCH_SIZE):
        await session.execute(insert(table), filled[i : i + _BATCH_SIZE])
    return len(filled)


async def hotness_for_symbols(
    session: AsyncSession,
    repository_id: str,
    symbol_ids: Iterable[str],
    *,
    files: Iterable[str] = (),
) -> dict[str, SymbolHotness]:
    """The row of each of *symbol_ids*, and of every symbol in *files*, that has one.

    A symbol missing from the result has no runtime evidence: unknown, never
    cold. A file is read as its key range. Each statement takes one chunk of
    ids and one chunk of ranges (``key_range_chunks``), under SQLite's bound
    variable limit, so a few symbols and files cost one keyed statement.
    """
    from .function_facts import key_range_chunks

    wanted = sorted(set(symbol_ids))
    sid = SymbolHotness.symbol_id
    id_chunks = [wanted[i : i + _BATCH_SIZE] for i in range(0, len(wanted), _BATCH_SIZE)]
    range_chunks = list(key_range_chunks(sid, files))
    out: dict[str, SymbolHotness] = {}
    for ids, ranges in zip_longest(id_chunks, range_chunks, fillvalue=[]):
        clauses: list[Any] = [sid.in_(ids)] if ids else []
        clauses += ranges
        result = await session.execute(
            select(SymbolHotness).where(
                SymbolHotness.repository_id == repository_id, or_(*clauses)
            )
        )
        out.update({row.symbol_id: row for row in result.scalars()})
    return out


async def production_window_claims(
    session: AsyncSession, repository_id: str, run_ids: Iterable[str]
) -> dict[str, str]:
    """``{run id: claim}`` for each of *run_ids* that is a stored production
    run: what its rows claim in words (``evidence.status.window_claim``).

    One keyed statement per chunk of ids, and none when *run_ids* is empty,
    so a caller that passes only its production rows' runs costs every other
    read nothing.
    """
    from ....evidence.status import production_window

    wanted = sorted(set(run_ids))
    out: dict[str, str] = {}
    for i in range(0, len(wanted), _BATCH_SIZE):
        runs = await session.execute(
            select(EvidenceRun).where(
                EvidenceRun.repository_id == repository_id,
                EvidenceRun.id.in_(wanted[i : i + _BATCH_SIZE]),
            )
        )
        for run in runs.scalars():
            window = production_window(run)
            if window is not None:
                out[run.id] = window["claim"]
    return out


async def measured_callers(
    session: AsyncSession, repository_id: str, rows: Iterable[SymbolHotness]
) -> dict[str, list[str]]:
    """Each row's production callers that its run measured calling it
    (``drivers``), best first by the call graph: the *callers* that
    :func:`~repowise.core.evidence.action_row.action_row` weighs by share.

    A caller counts when the call graph lists it as a caller and its file is
    production code (``code_origin``), as on the measured rows. The graph read
    is ``_callers_of``'s (every caller edge of each function); only the callers
    a run measured have their files judged. A row with no such caller is left
    out; rows that stored no ``drivers`` cost no read.
    """
    from ....evidence.ingest import load_origins
    from ....evidence.payloads import json_list
    from ....ingestion.symbol_identity import file_of_symbol
    from .performance import _callers_of

    measured: dict[str, set[str]] = {}
    for row in rows:
        named = {
            d["symbol"]
            for d in json_list(getattr(row, "drivers_json", None)) or ()
            if isinstance(d, dict) and isinstance(d.get("symbol"), str)
        }
        if named:
            measured[row.symbol_id] = named
    if not measured:
        return {}
    sources = await _callers_of(session, repository_id, sorted(measured))
    graph = {
        sid: [s for s in sources.get(sid, ()) if s in named] for sid, named in measured.items()
    }
    origins = await load_origins(
        session, repository_id, {file_of_symbol(s) for found in graph.values() for s in found}
    )
    out = {
        sid: [s for s in found if origins.get(file_of_symbol(s)) == "production"]
        for sid, found in graph.items()
    }
    return {sid: found for sid, found in out.items() if found}


async def _eager_defaults(
    session: AsyncSession, repository_id: str, symbols: list[str]
) -> list[dict[str, Any]]:
    """The eager default facts of the *symbols* whose walk found one
    (:func:`~repowise.core.evidence.next_step.eager_default_fact`)."""
    from ....evidence.next_step import eager_default_fact
    from ...models import FunctionFact

    out: list[dict[str, Any]] = []
    for i in range(0, len(symbols), _BATCH_SIZE):
        result = await session.execute(
            select(FunctionFact.symbol_id, FunctionFact.eager_defaults_json).where(
                FunctionFact.repository_id == repository_id,
                FunctionFact.symbol_id.in_(symbols[i : i + _BATCH_SIZE]),
                FunctionFact.eager_defaults_json.is_not(None),
            )
        )
        out.extend(filter(None, (eager_default_fact(sid, raw) for sid, raw in result.all())))
    return sorted(out, key=lambda f: f["symbol"])


async def step_findings(
    session: AsyncSession, repository_id: str, rows: Iterable[SymbolHotness]
) -> list[dict[str, Any]]:
    """The open performance findings a next step reads for *rows*
    (``evidence.next_step``): those in the rows' files and their drivers' files,
    and those whose call path names a row or a driver, as
    :func:`~repowise.core.evidence.next_step.static_finding` dicts, then the
    eager ``setdefault`` defaults the rows and drivers build
    (:func:`~repowise.core.evidence.next_step.eager_default_fact`). Facts in
    files that are not production code (``code_origin``) are left out, so a loop
    in a test never reads as an N+1. An import row that loaded a library the
    run never ran adds that package's production call sites
    (:func:`_package_calls`)."""
    import json

    from ....evidence.format_v1 import is_import_row
    from ....evidence.ingest import load_origins
    from ....evidence.next_step import never_called, static_finding
    from ....evidence.payloads import json_list
    from ....ingestion.symbol_identity import file_of_symbol
    from ...models import HealthFinding

    rows = list(rows)
    targets: set[str] = set()
    packages: set[str] = set()
    for row in rows:
        targets.add(row.symbol_id)
        if is_import_row(row.symbol_id):
            packages.update(never_called(json_list(getattr(row, "inside_json", None))))
        try:
            drivers = json.loads(getattr(row, "drivers_json", None) or "[]")
        except ValueError:
            drivers = []
        for driver in drivers if isinstance(drivers, list) else ():
            if isinstance(driver, dict) and isinstance(driver.get("symbol"), str):
                targets.add(driver["symbol"])
    if not targets:
        return []
    files = sorted({file_of_symbol(t) for t in targets})
    named = sorted(targets)
    # Half a batch of each per statement keeps the bound variables under the limit.
    step = _BATCH_SIZE // 2
    found: dict[str, HealthFinding] = {}
    for i in range(0, max(len(files), len(named)), step):
        clauses: list[Any] = []
        if files[i : i + step]:
            clauses.append(HealthFinding.file_path.in_(files[i : i + step]))
        clauses += [
            HealthFinding.details_json.contains(json.dumps(t), autoescape=True)
            for t in named[i : i + step]
        ]
        result = await session.execute(
            select(HealthFinding).where(
                HealthFinding.repository_id == repository_id,
                HealthFinding.dimension == "performance",
                HealthFinding.status == "open",
                or_(*clauses),
            )
        )
        found.update({f.id: f for f in result.scalars()})
    eager = await _eager_defaults(session, repository_id, named)
    paths = {f.file_path for f in found.values()} | {f["file_path"] for f in eager}
    origins = await load_origins(session, repository_id, paths)
    out = [
        static_finding(
            f.file_path, f.function_name, f.biomarker_type, f.reason, json.loads(f.details_json or "{}")
        )
        for f in sorted(found.values(), key=lambda f: (f.file_path, f.line_start or 0, f.id))
        if origins.get(f.file_path) == "production"
    ]
    return (
        out
        + [f for f in eager if origins.get(f["file_path"]) == "production"]
        + await _package_calls(session, repository_id, packages)
    )


async def _package_calls(
    session: AsyncSession, repository_id: str, packages: set[str]
) -> list[dict[str, Any]]:
    """Per Python package, its production call sites with each caller's symbol
    kind (:func:`~repowise.core.evidence.next_step.package_calls`). None until the
    stored call sites are complete, and none for a package nothing calls."""
    from ....evidence.ingest import load_origins
    from ....evidence.next_step import package_calls
    from ...models import WikiSymbol
    from ..external_call_sites import external_call_sites_complete, list_external_call_sites

    if not packages or not await external_call_sites_complete(session, repository_id):
        return []
    sites = await list_external_call_sites(
        session, repository_id, packages=[("pypi", p) for p in sorted(packages)]
    )
    origins = await load_origins(session, repository_id, {s.file_path for s in sites})
    sites = [s for s in sites if origins.get(s.file_path) == "production"]
    callers = sorted({s.caller_symbol_id for s in sites})
    kinds: dict[str, str] = {}
    for i in range(0, len(callers), _BATCH_SIZE):
        result = await session.execute(
            select(WikiSymbol.symbol_id, WikiSymbol.kind).where(
                WikiSymbol.repository_id == repository_id,
                WikiSymbol.symbol_id.in_(callers[i : i + _BATCH_SIZE]),
            )
        )
        kinds.update({sid: kind for sid, kind in result})
    by_package: dict[str, list[tuple[str, str | None]]] = {}
    for site in sites:
        by_package.setdefault(site.package, []).append(
            (site.caller_symbol_id, kinds.get(site.caller_symbol_id))
        )
    return [package_calls(p, found) for p, found in sorted(by_package.items())]


async def ranked_by_workload(
    session: AsyncSession, repository_id: str
) -> list[tuple[EvidenceRun, list[WorkloadHotness]]]:
    """Each workload's newest run that ranks any function, with its ranked
    functions, best rank first (:func:`newer_unranked` names a newer run
    that ranks nothing).

    Runs come newest first. Two statements: the ranked rows, then their runs.
    Empty when nothing is ranked.
    """
    rows = list(
        (
            await session.execute(
                select(WorkloadHotness)
                .where(WorkloadHotness.repository_id == repository_id)
                .order_by(WorkloadHotness.run_id, WorkloadHotness.rank, WorkloadHotness.symbol_id)
            )
        ).scalars()
    )
    if not rows:
        return []
    by_run: dict[str, list[WorkloadHotness]] = {}
    for row in rows:
        by_run.setdefault(row.run_id, []).append(row)
    runs = (
        await session.execute(select(EvidenceRun).where(EvidenceRun.id.in_(sorted(by_run))))
    ).scalars()
    found = sorted(runs, key=lambda r: (r.measured_at or r.ingested_at, r.id), reverse=True)
    return [(run, by_run[run.id]) for run in found]


async def statement_costs(session: AsyncSession, repository_id: str) -> list[StatementRun]:
    """Each production statement workload's newest run, newest first, with
    what each function that issues its statements cost in the window
    (``evidence.measured_fold.fold_statements``: only statements the join put
    on exactly one function count toward it; the rest are counted by label).

    Two statements: the ``statement_stats`` runs, then the rows of the ones
    that speak. Empty when no statement run is stored.
    """
    from ....evidence.measured_fold import fold_statements, query_runs_to_read

    runs = await _production_runs(session, repository_id, "statement_stats")
    keep = query_runs_to_read(runs)
    if not keep:
        return []
    points = await session.execute(
        select(
            EvidenceStatementStats.run_id,
            EvidenceStatementStats.symbol_id,
            EvidenceStatementStats.issuer,
            EvidenceStatementStats.calls,
            EvidenceStatementStats.total_ms,
        ).where(EvidenceStatementStats.run_id.in_(keep))
    )
    return fold_statements(runs, [tuple(p) for p in points])


async def route_costs(session: AsyncSession, repository_id: str) -> list[RouteRun]:
    """Each production route workload's newest run, newest first, with what
    each handler's requests cost in the window
    (``evidence.measured_fold.fold_routes``: only routes the route join put on
    a handler when the run was stored count toward it; the rest are counted
    as unjoined).

    Two statements: the ``route_latency`` runs, then the rows of the ones
    that speak. Empty when no route run is stored.
    """
    from ....evidence.measured_fold import fold_routes, query_runs_to_read

    runs = await _production_runs(session, repository_id, "route_latency")
    keep = query_runs_to_read(runs)
    if not keep:
        return []
    points = await session.execute(
        select(
            EvidenceRouteLatency.run_id,
            EvidenceRouteLatency.handler_symbol_id,
            EvidenceRouteLatency.method,
            EvidenceRouteLatency.route,
            EvidenceRouteLatency.request_count,
            EvidenceRouteLatency.total_ms,
            EvidenceRouteLatency.p95_ms,
            EvidenceRouteLatency.error_count,
        ).where(EvidenceRouteLatency.run_id.in_(keep))
    )
    return fold_routes(runs, [tuple(p) for p in points])


async def _production_runs(
    session: AsyncSession, repository_id: str, kind: str
) -> list[EvidenceRun]:
    """Every stored run of production-only *kind* (none of its rows)."""
    result = await session.execute(
        select(EvidenceRun).where(
            EvidenceRun.repository_id == repository_id,
            EvidenceRun.kind == kind,
        )
    )
    return list(result.scalars())


async def _statement_names_a_function(session: AsyncSession, repository_id: str) -> bool:
    """A production statement workload's newest run puts a statement on a
    function: what :func:`statement_costs` would list, without folding it."""
    from ....evidence.measured_fold import query_runs_to_read

    keep = query_runs_to_read(await _production_runs(session, repository_id, "statement_stats"))
    if not keep:
        return False
    named = select(EvidenceStatementStats.run_id).where(
        EvidenceStatementStats.run_id.in_(keep),
        EvidenceStatementStats.issuer == "function",
        EvidenceStatementStats.symbol_id.is_not(None),
    )
    return (await session.execute(named.limit(1))).first() is not None


async def _route_names_a_function(session: AsyncSession, repository_id: str) -> bool:
    """A production route workload's newest run puts a route on a handler:
    what :func:`route_costs` would list, without folding it."""
    from ....evidence.measured_fold import query_runs_to_read

    keep = query_runs_to_read(await _production_runs(session, repository_id, "route_latency"))
    if not keep:
        return False
    named = select(EvidenceRouteLatency.run_id).where(
        EvidenceRouteLatency.run_id.in_(keep),
        EvidenceRouteLatency.handler_symbol_id.is_not(None),
    )
    return (await session.execute(named.limit(1))).first() is not None


async def unranked_reason(session: AsyncSession, repository_id: str) -> str:
    """Why :func:`ranked_by_workload` has no row, as one token.

    ``no_runtime_data``: no profile run is stored outside the ``benchmark``
    class and no production statement or route run either (one that names
    no function reads ``not_ranked``). ``ahead_of_index``: every
    function the stored runs mapped is stale, and a workload's newest run was
    added after the newest run at the indexed commit
    (``evidence.hotness.commit_position``): it ranks once the index reaches
    its commit. ``stale``: every function the stored runs mapped is stale
    otherwise (measured at another commit, body changed since).
    ``needs_update``: a workload's newest run is the basis of ranked
    functions in the per-function view but the per-workload view holds
    nothing (a store from before it existed, until the next update or ingest
    rebuilds both). ``not_ranked``: current evidence that ranks nothing (too
    few samples in production code, or no production frame).
    """
    has_profile = select(EvidenceRun.id).where(
        EvidenceRun.repository_id == repository_id,
        or_(
            # A benchmark profile never ranks (``evidence.hotness``).
            (EvidenceRun.kind == "profile") & (EvidenceRun.workload_class != "benchmark"),
            EvidenceRun.kind.in_(("statement_stats", "route_latency")),
        ),
    )
    if (await session.execute(has_profile.limit(1))).first() is None:
        return "no_runtime_data"
    mapped = select(SymbolHotness.symbol_id).where(SymbolHotness.repository_id == repository_id)
    fresh = mapped.where(SymbolHotness.freshness != "stale")
    if (await session.execute(fresh.limit(1))).first() is not None:
        if await _workload_view_missing(session, repository_id):
            return "needs_update"
        return "not_ranked"
    if (await session.execute(mapped.limit(1))).first() is not None:
        if await _newest_ahead_of_index(session, repository_id):
            return "ahead_of_index"
        return "stale"
    return "not_ranked"


async def newer_unranked(
    session: AsyncSession, repository_id: str, listed: Iterable[Any]
) -> dict[str, dict[str, Any]]:
    """For each of *listed* (runs :func:`ranked_by_workload` returned) that is
    not its workload's newest complete run, that newest run, which ranks
    nothing, and why, by the listed run's id.

    ``reason`` is ``ahead_of_index`` when the newer run was added after
    the newest run at the indexed commit (``evidence.hotness.commit_position``:
    it ranks once the index reaches its commit), ``not_ranked`` when it is at
    the indexed commit (too few samples in production code, or no production
    frame), else ``stale`` (another commit whose bodies changed since).
    Ceiling: a newer run at another commit is judged by its commit alone; one
    whose rows still match the index but are too thin to rank reads as ahead
    or stale. Two statements, none of a run's rows.
    """
    from ....evidence.hotness import _age_key, commit_position

    listed = list(listed)
    if not listed:
        return {}
    runs, head = await _run_marks(session, repository_id)
    out: dict[str, dict[str, Any]] = {}
    for run in listed:
        same = (run.kind, run.workload_name, run.environment)
        newer = [
            r
            for r in runs
            if (r.kind, r.workload_name, r.environment) == same
            and not r.mapping_partial
            and _age_key(r) > _age_key(run)
        ]
        if not newer:
            continue
        top = max(newer, key=_age_key)
        position = commit_position(top, runs, head)
        out[run.id] = {
            "run_id": top.id,
            "commit": top.commit_sha,
            "measured_at": _utc_iso(top.measured_at or top.ingested_at),
            "reason": _SKIPPED_REASON.get(position, "stale"),
        }
    return out


#: Why a workload's newer run ranks nothing, by where its commit sits.
_SKIPPED_REASON = {"ahead": "ahead_of_index", "indexed": "not_ranked"}


def _utc_iso(at: Any) -> str | None:
    # SQLite hands back the stored UTC time without its zone.
    return at.replace(tzinfo=at.tzinfo or UTC).isoformat() if at else None


async def _run_marks(session: AsyncSession, repository_id: str) -> tuple[list[Any], str | None]:
    """Every stored run's kind, workload, environment, commit and times (none
    of its rows), and the indexed commit: what ``commit_position`` reads."""
    from ...models import Repository

    head = (
        await session.execute(select(Repository.head_commit).where(Repository.id == repository_id))
    ).scalar_one_or_none()
    result = await session.execute(
        select(
            EvidenceRun.id,
            EvidenceRun.kind,
            EvidenceRun.workload_name,
            EvidenceRun.workload_class,
            EvidenceRun.environment,
            EvidenceRun.commit_sha,
            EvidenceRun.measured_at,
            EvidenceRun.ingested_at,
            EvidenceRun.mapping_partial,
        ).where(EvidenceRun.repository_id == repository_id)
    )
    return list(result), head


async def _newest_ahead_of_index(session: AsyncSession, repository_id: str) -> bool:
    """A profile workload's newest complete run is ahead of the index (a
    benchmark profile never ranks, so it is not one)."""
    from ....evidence.hotness import BENCHMARK_CLASS, commit_position, newest_runs

    runs, head = await _run_marks(session, repository_id)
    newest = newest_runs(
        r
        for r in runs
        if r.kind == "profile" and r.workload_class != BENCHMARK_CLASS and not r.mapping_partial
    )
    return any(commit_position(r, runs, head) == "ahead" for r in runs if r.id in newest)


async def measured_status(session: AsyncSession, repository_id: str) -> str:
    """``measured`` when a workload's run ranks a function (what
    :func:`ranked_by_workload` lists) or a production statement workload's
    newest run puts a statement on a function (:func:`statement_costs`) or a
    production route workload's newest run puts a route on a handler
    (:func:`route_costs`), else :func:`unranked_reason`'s token. One indexed
    lookup when a profile ranks anything; two more per production kind (its
    runs, one row) otherwise."""
    ranked = select(WorkloadHotness.run_id).where(WorkloadHotness.repository_id == repository_id)
    if (await session.execute(ranked.limit(1))).first() is not None:
        return "measured"
    if await _statement_names_a_function(session, repository_id):
        return "measured"
    if await _route_names_a_function(session, repository_id):
        return "measured"
    return await unranked_reason(session, repository_id)


def unranked_command(status: str) -> str | None:
    """The command that moves an :func:`unranked_reason` token on: ``repowise
    update`` builds a per-workload view that was never built, or brings the
    index to the commit that evidence ahead of it measured; evidence setup
    connects evidence when none is stored or all of it is stale. ``None`` for
    ``not_ranked`` and ``measured``: no command ranks thin evidence."""
    if status in ("needs_update", "ahead_of_index"):
        return "repowise update"
    if status in ("no_runtime_data", "stale"):
        from ....evidence.setup import SETUP_COMMAND

        return SETUP_COMMAND
    return None


async def _workload_view_missing(session: AsyncSession, repository_id: str) -> bool:
    """A run is the basis of a fresh ranked function, yet ``workload_hotness``
    is empty: the per-workload view was never built.

    Built, it lists each workload's newest run that ranks any function, so a
    run that ranked one would have rows there whatever runs came after it.
    """
    built = select(WorkloadHotness.run_id).where(WorkloadHotness.repository_id == repository_id)
    if (await session.execute(built.limit(1))).first() is not None:
        return False
    ranked = select(SymbolHotness.symbol_id).where(
        SymbolHotness.repository_id == repository_id,
        SymbolHotness.tier.in_(("ranked", "lead_candidate")),
        SymbolHotness.freshness != "stale",
    )
    return (await session.execute(ranked.limit(1))).first() is not None


def _refuse_repeated_keys(table: Table, rows: list[dict[str, Any]]) -> None:
    key = [column.name for column in table.primary_key.columns]
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        values = tuple(row[name] for name in key)
        if values in seen:
            raise ValueError(f"{table.name} rows repeat the key {values[1:]!r}")
        seen.add(values)


def _full_row(table: Table, row: Mapping[str, Any]) -> dict[str, Any]:
    """*row* with every column of *table* present, NULL where it says nothing.

    One bulk insert needs one key set across its rows; a name that is not a
    column is a writer bug, so it raises rather than being dropped.
    """
    names = [column.name for column in table.c]
    unknown = set(row) - set(names)
    if unknown:
        raise ValueError(f"not columns of {table.name}: {sorted(unknown)}")
    full = {name: row.get(name) for name in names}
    for column in table.c:
        default = column.default
        if full[column.name] is None and default is not None and not column.nullable:
            full[column.name] = default.arg(None) if callable(default.arg) else default.arg
    return full


__all__ = [
    "EVIDENCE_ROW_TABLES",
    "EvidenceWrite",
    "benchmark_callees",
    "benchmark_results",
    "benchmark_runs",
    "delete_evidence_runs",
    "evidence_row_table",
    "hotness_for_symbols",
    "hotness_inputs",
    "list_evidence_runs",
    "measured_status",
    "newer_unranked",
    "production_window_claims",
    "prune_evidence_runs",
    "ranked_by_workload",
    "replace_symbol_hotness",
    "replace_workload_hotness",
    "route_costs",
    "save_evidence_run",
    "statement_costs",
    "unranked_command",
    "unranked_reason",
]
