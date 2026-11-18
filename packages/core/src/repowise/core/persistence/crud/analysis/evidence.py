"""CRUD operations for the runtime evidence store (``evidence_*`` tables).

One ingest writes one ``evidence_runs`` row and that run's rows of its kind,
in bulk. Pruning deletes whole runs by id, one statement per table.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Table, delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (
    EvidenceBenchmarkResult,
    EvidenceQueryCount,
    EvidenceRun,
    EvidenceSymbolCost,
    EvidenceTestTiming,
    _new_uuid,
    _now_utc,
)
from .._shared import _BATCH_SIZE

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
    "list_evidence_runs",
    "prune_evidence_runs",
    "save_evidence_run",
]
