"""CRUD operations for the runtime evidence store (``evidence_*`` tables).

One ingest writes one ``evidence_runs`` row and that run's rows of its kind,
in bulk. Pruning deletes whole runs by id, one statement per table.

``symbol_hotness`` is replaced whole per repository (one delete, bulk
inserts) by ``repowise.core.evidence.hotness``; :func:`hotness_for_symbols`
and :func:`ranked_hotness` are the readers every surface goes through.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Table, delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (
    EvidenceBenchmarkResult,
    EvidenceQueryCount,
    EvidenceRun,
    EvidenceSymbolCost,
    EvidenceTestTiming,
    SymbolHotness,
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
    table = cast(Table, SymbolHotness.__table__)
    at = _now_utc()
    filled = [
        _full_row(table, {**row, "repository_id": repository_id, "built_at": at}) for row in rows
    ]
    _refuse_repeated_keys(table, filled)
    await session.execute(delete(SymbolHotness).where(SymbolHotness.repository_id == repository_id))
    for i in range(0, len(filled), _BATCH_SIZE):
        await session.execute(insert(table), filled[i : i + _BATCH_SIZE])
    return len(filled)


async def hotness_for_symbols(
    session: AsyncSession, repository_id: str, symbol_ids: Iterable[str]
) -> dict[str, SymbolHotness]:
    """The ``symbol_hotness`` row of each of *symbol_ids* that has one.

    A symbol missing from the result has no runtime evidence: unknown, never cold.
    """
    wanted = sorted(set(symbol_ids))
    out: dict[str, SymbolHotness] = {}
    for i in range(0, len(wanted), _BATCH_SIZE):
        result = await session.execute(
            select(SymbolHotness).where(
                SymbolHotness.repository_id == repository_id,
                SymbolHotness.symbol_id.in_(wanted[i : i + _BATCH_SIZE]),
            )
        )
        out.update({row.symbol_id: row for row in result.scalars()})
    return out


async def ranked_hotness(
    session: AsyncSession,
    repository_id: str,
    *,
    workload_classes: Collection[str] | None = None,
    limit: int = 10,
) -> list[SymbolHotness]:
    """The repository's ranked functions, strongest first, at most *limit*.

    Ranked means tier ``ranked`` or ``lead_candidate`` from evidence that is
    not stale. *workload_classes* keeps rows whose basis run has one of those
    classes (``{"unit_test"}`` for what was measured in tests). Each row
    carries its run's summary: workload, commit, age, explained share, sample
    counts and how many functions outside production code it counted.
    """
    query = select(SymbolHotness).where(
        SymbolHotness.repository_id == repository_id,
        SymbolHotness.tier.in_(("ranked", "lead_candidate")),
        SymbolHotness.freshness != "stale",
    )
    if workload_classes is not None:
        query = query.where(SymbolHotness.workload_class.in_(sorted(workload_classes)))
    rows = list((await session.execute(query)).scalars())
    rows.sort(
        key=lambda r: (
            r.tier != "lead_candidate",
            r.rank if r.rank is not None else float("inf"),
            -(r.total_share or 0.0),
            r.symbol_id,
        )
    )
    return rows[:limit]


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
    "prune_evidence_runs",
    "ranked_hotness",
    "replace_symbol_hotness",
    "save_evidence_run",
]
