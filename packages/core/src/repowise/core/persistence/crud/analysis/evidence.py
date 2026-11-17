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
) -> EvidenceWrite:
    """Store one run and its rows, then keep the newest *history_runs* of its kind.

    *run* holds ``EvidenceRun`` columns (``kind`` and ``run_key`` required);
    *rows* hold the columns of the kind's table, without ``run_id`` and
    ``repository_id``. A run whose ``run_key`` and commit match a stored run
    is one measurement ingested twice: the stored run is restamped and
    nothing else is written, like ``coverage._record_ingest``. A run with no
    commit is always new, since it cannot be told apart from another.
    """
    kind = run["kind"]
    table = EVIDENCE_ROW_TABLES.get(kind)
    if table is None:
        raise ValueError(f"unknown evidence kind {kind!r}")
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

    run_id = _new_uuid()
    await session.execute(
        insert(EvidenceRun.__table__),
        [
            _full_row(
                EvidenceRun.__table__,
                {**run, "id": run_id, "ingested_at": at, "repository_id": repository_id},
            )
        ],
    )
    filled = [
        _full_row(table, {**row, "run_id": run_id, "repository_id": repository_id})
        for row in rows
    ]
    # On the table, not the mapper: the ORM bulk path splits a batch wherever
    # a column flips between NULL and a value.
    for i in range(0, len(filled), _BATCH_SIZE):
        await session.execute(insert(table), filled[i : i + _BATCH_SIZE])
    pruned = await prune_evidence_runs(session, repository_id, kind, keep=history_runs)
    return EvidenceWrite(run_id, restamped=False, rows_written=len(filled), runs_pruned=pruned)


async def prune_evidence_runs(
    session: AsyncSession, repository_id: str, kind: str, *, keep: int
) -> int:
    """Drop the runs of *kind* past the newest *keep*, oldest first. Returns how many."""
    stale = list(
        (
            await session.execute(
                select(EvidenceRun.id)
                .where(EvidenceRun.repository_id == repository_id, EvidenceRun.kind == kind)
                .order_by(EvidenceRun.ingested_at.desc(), EvidenceRun.id.desc())
                .offset(max(keep, 0))
            )
        ).scalars()
    )
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
    "prune_evidence_runs",
    "save_evidence_run",
]
