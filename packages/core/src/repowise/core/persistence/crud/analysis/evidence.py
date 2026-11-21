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
from itertools import zip_longest
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Table, delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (
    EvidenceBenchmarkResult,
    EvidenceQueryCount,
    EvidenceRun,
    EvidenceSymbolCost,
    EvidenceTestTiming,
    SymbolHotness,
    WorkloadHotness,
    _new_uuid,
    _now_utc,
)
from .._shared import _BATCH_SIZE

if TYPE_CHECKING:
    from ....evidence.hotness import CostFacts, RunFacts

#: The table that holds a run's rows, by the run's evidence kind.
EVIDENCE_ROW_TABLES: dict[str, Table] = {
    "profile": EvidenceSymbolCost.__table__,  # type: ignore[dict-item]
    "benchmark": EvidenceBenchmarkResult.__table__,  # type: ignore[dict-item]
    "test_timing": EvidenceTestTiming.__table__,  # type: ignore[dict-item]
    "query_count": EvidenceQueryCount.__table__,  # type: ignore[dict-item]
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
    must cover everything the run row records. A run with no commit is
    always new, since it cannot be told apart from another. Pruning keeps
    *history_runs* per workload and *max_runs* per kind, never dropping a
    workload's newest run (:func:`prune_evidence_runs`).
    """
    kind = run["kind"]
    table = EVIDENCE_ROW_TABLES.get(kind)
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
    if commit is not None:
        same = (
            await session.execute(
                select(EvidenceRun)
                .where(
                    EvidenceRun.repository_id == repository_id,
                    EvidenceRun.run_key == run["run_key"],
                    EvidenceRun.commit_sha == commit,
                )
                .order_by(EvidenceRun.ingested_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if same is not None:
            same.ingested_at = at
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
        for table in EVIDENCE_ROW_TABLES.values():
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


async def ranked_by_workload(
    session: AsyncSession, repository_id: str
) -> list[tuple[EvidenceRun, list[WorkloadHotness]]]:
    """Each workload's newest run with its ranked functions, best rank first.

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


async def unranked_reason(session: AsyncSession, repository_id: str) -> str:
    """Why :func:`ranked_by_workload` has no row, as one token.

    ``no_runtime_data``: no profile run is stored. ``stale``: every function
    the stored runs mapped is stale (measured at another commit, body changed
    since). ``needs_update``: a workload's newest run is the basis of ranked
    functions in the per-function view but the per-workload view holds
    nothing (a store from before it existed, until the next update or ingest
    rebuilds both); a
    newest run that ranks nothing beside an older one that did is
    ``not_ranked``, never this. ``not_ranked``: current evidence that ranks
    nothing (too few samples in production code, or no production frame).
    """
    has_profile = select(EvidenceRun.id).where(
        EvidenceRun.repository_id == repository_id, EvidenceRun.kind == "profile"
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
        return "stale"
    return "not_ranked"


async def measured_status(session: AsyncSession, repository_id: str) -> str:
    """``measured`` when a workload's newest run ranks a function (what
    :func:`ranked_by_workload` lists), else :func:`unranked_reason`'s token.
    One indexed lookup when anything is ranked."""
    ranked = select(WorkloadHotness.run_id).where(WorkloadHotness.repository_id == repository_id)
    if (await session.execute(ranked.limit(1))).first() is not None:
        return "measured"
    return await unranked_reason(session, repository_id)


def unranked_command(status: str) -> str | None:
    """The command that moves an :func:`unranked_reason` token on: ``repowise
    update`` builds a per-workload view that was never built, evidence setup
    connects evidence when none is stored or all of it is stale. ``None`` for
    ``not_ranked`` and ``measured``: no command ranks thin evidence."""
    if status == "needs_update":
        return "repowise update"
    if status in ("no_runtime_data", "stale"):
        from ....evidence.setup import SETUP_COMMAND

        return SETUP_COMMAND
    return None


async def _workload_view_missing(session: AsyncSession, repository_id: str) -> bool:
    """A workload's newest run is the basis of a ranked function, yet
    ``workload_hotness`` is empty: the per-workload view was never built.

    Built, that run's ranked functions would be rows there, so an empty view
    beside ranked older runs only (a thin newest run) is not this.

    Sound, not complete: a function keeps one basis run across all runs, so
    a newest run whose every ranked function another run ranks better (a
    lead class, a better place) is the basis of nothing, and a never built
    view then reads as ``not_ranked``. Nothing stored says whether a run
    ranked a function it is not the basis of: that takes the run's place
    and floor, which only the derivation knows. Counting any function a
    newest run measured instead would answer ``needs_update`` after every
    update for a thin newest run, so the miss is kept: it lasts only until
    the next update or ingest rebuilds both views. A stored marker that the
    view was built would make this exact.
    """
    from types import SimpleNamespace

    from ....evidence.hotness import newest_runs

    built = select(WorkloadHotness.run_id).where(WorkloadHotness.repository_id == repository_id)
    if (await session.execute(built.limit(1))).first() is not None:
        return False
    result = await session.execute(
        select(
            EvidenceRun.id,
            EvidenceRun.workload_name,
            EvidenceRun.environment,
            EvidenceRun.measured_at,
            EvidenceRun.ingested_at,
        ).where(
            EvidenceRun.repository_id == repository_id,
            EvidenceRun.kind == "profile",
            EvidenceRun.mapping_partial.is_(False),
        )
    )
    newest = newest_runs(
        SimpleNamespace(
            id=r.id,
            workload_name=r.workload_name,
            environment=r.environment,
            measured_at=r.measured_at or r.ingested_at,
        )
        for r in result
    )
    ranked = select(SymbolHotness.symbol_id).where(
        SymbolHotness.repository_id == repository_id,
        SymbolHotness.tier.in_(("ranked", "lead_candidate")),
        SymbolHotness.freshness != "stale",
        SymbolHotness.run_id.in_(sorted(newest)),
    )
    return bool(newest) and (await session.execute(ranked.limit(1))).first() is not None


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
    "delete_evidence_runs",
    "hotness_for_symbols",
    "hotness_inputs",
    "list_evidence_runs",
    "measured_status",
    "prune_evidence_runs",
    "ranked_by_workload",
    "replace_symbol_hotness",
    "replace_workload_hotness",
    "save_evidence_run",
    "unranked_command",
    "unranked_reason",
]
