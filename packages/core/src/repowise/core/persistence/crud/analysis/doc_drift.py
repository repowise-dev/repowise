"""CRUD operations for documentation drift findings."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.doc_drift.constants import bucket_confidences

from ...models import DocDriftFinding
from .._shared import _BATCH_SIZE

#: Width of the two ``String(1024)`` columns. ``target`` is lifted verbatim
#: from a document rather than minted by the traverser, so a pathological
#: reference (a long inline URL, a data: URI) can exceed it. SQLite ignores
#: the declared width and Postgres raises ``StringDataRightTruncation``, so
#: leaving it unclamped is a backend divergence that surfaces as total loss of
#: the write on the one backend the architecture docs recommend.
_PATH_COLUMN_WIDTH = 1024

#: Chunk for ``IN (...)`` deletes. Lower than ``_BATCH_SIZE`` deliberately:
#: the house figure for a parameterised ``IN`` clause is 400
#: (``security_scan.py`` and ``_shared._GATE_LOAD_CHUNK`` both use it) for
#: SQLite parameter-limit headroom.
_IN_CLAUSE_CHUNK = 400


def _row_kwargs(finding: Any, repository_id: str) -> dict:
    """Normalize a ``DocDriftFindingData`` into ORM row kwargs."""
    kind = finding.kind
    return {
        "repository_id": repository_id,
        "file_path": finding.file_path[:_PATH_COLUMN_WIDTH],
        "kind": str(kind.value) if hasattr(kind, "value") else str(kind),
        "line_number": int(finding.line_number),
        "target": finding.target[:_PATH_COLUMN_WIDTH],
        "confidence": float(finding.confidence),
        "reason": finding.reason,
        "origin": finding.origin,
        "evidence_json": json.dumps(list(finding.evidence or [])),
        "raw": finding.raw,
        "context": finding.context,
    }


async def replace_doc_drift_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
    *,
    scope: frozenset[str] | set[str] | None = None,
) -> int:
    """Replace this repository's drift findings, converging on re-run.

    Delete-then-insert, like dead code and security before it. ``scope``, when
    given, names the document paths this write may speak for: an incremental
    re-check that only re-read four documents must not delete findings for the
    other three hundred it never looked at. ``None`` means the run was
    repo-wide and every row is replaceable.

    Returns the number of rows inserted.

    Idempotency rests on two things. The unique constraint
    ``uq_doc_drift_finding_site`` is the durable one. The other is that the
    DELETE scope and the INSERT scope are identical --- every row inserted
    belongs to a path just deleted --- so no surviving row can collide. The
    Python-side dedup below closes the remaining gap, which is two findings in
    the *same batch* sharing a site; without it that batch would raise after
    the DELETE had already run. ``security_scan.replace_findings`` guards the
    same hazard the same way, and additionally uses a conflict-tolerant
    INSERT because it has a second writer (the history scan) that this table
    does not.
    """
    scoped = frozenset(scope) if scope is not None else None

    if scoped is None:
        await session.execute(
            delete(DocDriftFinding).where(DocDriftFinding.repository_id == repository_id)
        )
    else:
        # Chunked so a large scope stays under SQLite's parameter limit.
        paths = sorted(scoped)
        for i in range(0, len(paths), _IN_CLAUSE_CHUNK):
            chunk = paths[i : i + _IN_CLAUSE_CHUNK]
            await session.execute(
                delete(DocDriftFinding).where(
                    DocDriftFinding.repository_id == repository_id,
                    DocDriftFinding.file_path.in_(chunk),
                )
            )

    rows: list[dict] = []
    seen: set[tuple] = set()
    for finding in findings or []:
        if scoped is not None and finding.file_path not in scoped:
            # A finding outside the scope would be inserted and never deleted
            # by the next scoped run, so it would outlive its own evidence.
            continue
        kwargs = _row_kwargs(finding, repository_id)
        key = (
            kwargs["file_path"],
            kwargs["kind"],
            kwargs["line_number"],
            kwargs["target"],
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(kwargs)

    for i in range(0, len(rows), _BATCH_SIZE):
        session.add_all(DocDriftFinding(**r) for r in rows[i : i + _BATCH_SIZE])
        await session.flush()

    return len(rows)


async def replace_doc_drift_findings_guarded(
    session: AsyncSession,
    repository_id: str,
    report: Any,
) -> int:
    """:func:`replace_doc_drift_findings` inside a savepoint, from a *report*.

    Every caller reports failure as a warning rather than raising, and this is a
    DELETE followed by an INSERT. Without the savepoint a failing insert leaves
    the DELETE buffered in the caller's live transaction, which then commits it:
    every drift row for the repository wiped, reported only as a warning. On
    Postgres the same failure poisons the transaction and takes the other
    analyses down with it.
    """
    async with session.begin_nested():
        return await replace_doc_drift_findings(
            session,
            repository_id,
            report.findings,
            scope=report.authoritative_paths,
        )


async def get_doc_drift_findings(
    session: AsyncSession,
    repository_id: str,
    *,
    file_path: str | None = None,
    min_confidence: float | None = None,
) -> list[DocDriftFinding]:
    """Read findings back, most confident first."""
    stmt = select(DocDriftFinding).where(DocDriftFinding.repository_id == repository_id)
    if file_path is not None:
        stmt = stmt.where(DocDriftFinding.file_path == file_path)
    if min_confidence is not None:
        stmt = stmt.where(DocDriftFinding.confidence >= min_confidence)
    stmt = stmt.order_by(
        DocDriftFinding.confidence.desc(),
        DocDriftFinding.file_path,
        DocDriftFinding.line_number,
    )
    return list((await session.execute(stmt)).scalars().all())


def _decode_evidence(raw: str) -> list[str]:
    """The stored evidence lines, degrading to none rather than raising.

    Pairs with the ``json.dumps`` in :func:`_row_kwargs`. An index written by an
    older version is still readable, and a finding whose evidence blob will not
    parse is worth more to a reader than a traceback.
    """
    try:
        loaded = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in loaded] if isinstance(loaded, list) else []


def serialize_doc_drift_row(row: DocDriftFinding, *, evidence: bool = True) -> dict:
    """One stored finding as a dict, for any surface that serves it.

    One function rather than one per surface. The CLI and ``get_health`` serve
    the same rows, and two hand-maintained dicts are how they would come to
    disagree about them --- the failure
    ``tests/unit/dead_code/test_confidence_parity.py`` exists to remember.
    Placed beside :func:`summarize_confidence_rows` because both are read-side
    derivations over a row; the no-serializer rule in
    ``analysis/doc_drift/models.py`` is about the analyzer's dataclass, which
    neither of these touches.

    ``evidence=False`` drops the evidence lines for a caller under a response
    budget: their first line restates ``file_path``, ``line_number`` and
    ``raw``, and the rest is the resolver's own trace.
    """
    out = {
        "file_path": row.file_path,
        "line_number": row.line_number,
        "kind": row.kind,
        "target": row.target,
        # Rounded in one place, so a future three-decimal tier cannot make one
        # surface report 0.925 where the other reports 0.93.
        "confidence": round(row.confidence, 2),
        "origin": row.origin,
        "reason": row.reason,
        "raw": row.raw,
        "context": row.context,
    }
    if evidence:
        out["evidence"] = _decode_evidence(row.evidence_json)
    return out


def summarize_confidence_rows(rows: list[DocDriftFinding]) -> dict:
    """Re-derive the high/medium/low buckets on read.

    Calls the same function the analyzer does, so the summary a reader sees
    cannot disagree with the one the pass computed.
    """
    return bucket_confidences(r.confidence for r in rows)
