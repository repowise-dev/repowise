"""CRUD operations for documentation drift: findings and references."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.doc_drift.constants import bucket_confidences
from repowise.core.analysis.doc_drift.serialize import finding_dict, reference_dict

from ...models import DocDriftFinding, DocDriftReference
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


@dataclass(frozen=True)
class _DriftTable:
    """Everything that differs between the two drift tables, in one place.

    Declared side by side below so the two stay legible as variants of one
    write rather than as two functions that happen to look alike.
    """

    model: Any
    """The ORM model rows are written to."""
    path_column: Any
    """Its document-path column, which the scoped delete narrows on."""
    path_of: Callable[[Any], str]
    """The document path of an analyzer item, for the scope check."""
    row_kwargs: Callable[[Any, str], dict]
    """One analyzer item as ORM row kwargs."""
    key_fields: tuple[str, ...]
    """The table's unique-constraint columns, for the in-batch dedup."""


_FINDINGS_TABLE = _DriftTable(
    model=DocDriftFinding,
    path_column=DocDriftFinding.file_path,
    path_of=lambda f: f.file_path,
    row_kwargs=lambda f, repo_id: _row_kwargs(f, repo_id),
    key_fields=("file_path", "kind", "line_number", "target"),
)

_REFERENCES_TABLE = _DriftTable(
    model=DocDriftReference,
    path_column=DocDriftReference.document_path,
    path_of=lambda r: r.doc_path,
    row_kwargs=lambda r, repo_id: _reference_row_kwargs(r, repo_id),
    key_fields=("document_path", "kind", "line_number", "target_path"),
)


async def _replace_scoped(
    session: AsyncSession,
    table: _DriftTable,
    repository_id: str,
    items: list[Any],
    *,
    scoped: frozenset[str] | None,
) -> int:
    """Delete-then-insert one drift table, converging on re-run.

    Shared by both so their scoping cannot drift apart: the two writes must
    speak for the same set of documents, or one describes a run the other
    never made.

    ``scoped`` names the document paths this write may speak for; ``None``
    means repo-wide. Idempotency rests on the unique constraint, and on the
    DELETE and INSERT scopes being identical so no surviving row can collide.
    The dedup closes the last gap: two items in one batch sharing a site,
    which would otherwise raise after the DELETE had run.
    """
    model = table.model
    if scoped is None:
        await session.execute(delete(model).where(model.repository_id == repository_id))
    else:
        # Chunked so a large scope stays under SQLite's parameter limit.
        paths = sorted(scoped)
        for i in range(0, len(paths), _IN_CLAUSE_CHUNK):
            chunk = paths[i : i + _IN_CLAUSE_CHUNK]
            await session.execute(
                delete(model).where(
                    model.repository_id == repository_id,
                    table.path_column.in_(chunk),
                )
            )

    rows: list[dict] = []
    seen: set[tuple] = set()
    for item in items or []:
        if scoped is not None and table.path_of(item) not in scoped:
            # Outside the scope it would be inserted and never deleted by the
            # next scoped run, so it would outlive its own evidence.
            continue
        kwargs = table.row_kwargs(item, repository_id)
        # Keyed on the truncated values, so two targets differing only past the
        # column width collapse here rather than colliding in the database.
        key = tuple(kwargs[field] for field in table.key_fields)
        if key in seen:
            continue
        seen.add(key)
        rows.append(kwargs)

    for i in range(0, len(rows), _BATCH_SIZE):
        session.add_all(model(**r) for r in rows[i : i + _BATCH_SIZE])
        await session.flush()

    return len(rows)


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
    """
    return await _replace_scoped(
        session,
        _FINDINGS_TABLE,
        repository_id,
        findings,
        scoped=frozenset(scope) if scope is not None else None,
    )


def _reference_row_kwargs(reference: Any, repository_id: str) -> dict:
    """Normalize a ``ResolvedDocReference`` into ORM row kwargs."""
    kind = reference.kind
    return {
        "repository_id": repository_id,
        "document_path": reference.doc_path[:_PATH_COLUMN_WIDTH],
        "target_path": reference.target_path[:_PATH_COLUMN_WIDTH],
        "kind": str(kind.value) if hasattr(kind, "value") else str(kind),
        "line_number": int(reference.line),
        "section": reference.section or "",
    }


async def replace_doc_drift_references(
    session: AsyncSession,
    repository_id: str,
    references: list[Any],
    *,
    scope: frozenset[str] | set[str] | None = None,
) -> int:
    """Replace this repository's resolved references, converging on re-run.

    The same contract as :func:`replace_doc_drift_findings`, over the same
    ``scope`` of document paths. Returns the number of rows inserted.
    """
    return await _replace_scoped(
        session,
        _REFERENCES_TABLE,
        repository_id,
        references,
        scoped=frozenset(scope) if scope is not None else None,
    )


async def replace_doc_drift_guarded(
    session: AsyncSession,
    repository_id: str,
    report: Any,
) -> int:
    """Write both drift tables from a *report*, inside one savepoint.

    One writer, because the two are complements of a single pass: written
    separately they could describe different runs, and a reverse view built on
    references from one and findings from another is the false "your
    documentation is wrong" this detector exists to avoid.

    Callers report failure as a warning rather than raising, so without the
    savepoint a failing insert leaves the DELETE buffered in their live
    transaction, which then commits it: every drift row wiped, reported as one
    warning. On Postgres it also poisons the transaction.

    Returns the number of findings inserted.
    """
    async with session.begin_nested():
        written = await replace_doc_drift_findings(
            session,
            repository_id,
            report.findings,
            scope=report.authoritative_paths,
        )
        await replace_doc_drift_references(
            session,
            repository_id,
            report.resolved_references,
            scope=report.authoritative_paths,
        )
        return written


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


async def get_doc_drift_references(
    session: AsyncSession,
    repository_id: str,
    *,
    target_paths: Sequence[str] | None = None,
) -> list[DocDriftReference]:
    """Read resolved references back, document order then line order.

    *target_paths* is the reverse question this table exists for, and it rides
    the target index. Plural because one query over the caller's whole batch
    beats one per target on a session they share. ``kind`` breaks the tie a
    markdown link with a fragment creates, which would otherwise be ordered by
    whatever the backend picks.
    """
    stmt = select(DocDriftReference).where(
        DocDriftReference.repository_id == repository_id
    )
    if target_paths is not None:
        paths = sorted(set(target_paths))
        if not paths:
            return []
        if len(paths) <= _IN_CLAUSE_CHUNK:
            stmt = stmt.where(DocDriftReference.target_path.in_(paths))
        else:
            # Chunked so a large batch stays under SQLite's parameter limit.
            rows: list[DocDriftReference] = []
            for i in range(0, len(paths), _IN_CLAUSE_CHUNK):
                rows.extend(
                    await get_doc_drift_references(
                        session,
                        repository_id,
                        target_paths=paths[i : i + _IN_CLAUSE_CHUNK],
                    )
                )
            return rows
    stmt = stmt.order_by(
        DocDriftReference.document_path,
        DocDriftReference.line_number,
        DocDriftReference.kind,
    )
    return list((await session.execute(stmt)).scalars().all())


async def doc_drift_references_stored(session: AsyncSession, repository_id: str) -> bool:
    """Whether this repository has any stored reference at all.

    What stops an empty answer being read as a clean one. The reconciler
    creates the table, but the pass only runs when an update has work to do,
    so "upgraded, never analysed" is a real state that looks exactly like "no
    document mentions this file".

    A repository with resolvable documentation has thousands of these rows, so
    their absence means the pass has not run. One whose documents resolve to
    nothing reads as unavailable rather than empty: an understatement, never a
    false clean, which is the direction this must fail in.
    """
    stmt = select(DocDriftReference.id).where(
        DocDriftReference.repository_id == repository_id
    )
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def doc_drift_findings_stored(session: AsyncSession, repository_id: str) -> bool:
    """Whether this repository has any stored finding at all.

    The findings half of :func:`doc_drift_references_stored`, and it exists for
    the case that one cannot cover: a repository whose documents resolve to
    nothing has no reference rows, so the reference probe alone would report a
    tree that *does* carry findings as never analysed. Either row anywhere is
    proof the pass ran.

    Asked only when a narrowed query came back empty, so the common case pays
    nothing.
    """
    stmt = select(DocDriftFinding.id).where(
        DocDriftFinding.repository_id == repository_id
    )
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


def serialize_doc_drift_reference_row(row: DocDriftReference) -> dict:
    """One stored reference as a dict, for any surface that serves it.

    The row half of :func:`~repowise.core.analysis.doc_drift.serialize.reference_dict`,
    which holds the key names and the reasons for them. Delegating rather than
    restating is what makes a store-backed answer and a report-backed one the
    same bytes.
    """
    return reference_dict(
        document_path=row.document_path,
        kind=row.kind,
        line_number=row.line_number,
        section=row.section,
    )


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

    The row half of :func:`~repowise.core.analysis.doc_drift.serialize.finding_dict`,
    which holds the keys and the rounding. All this adds is the JSON decode the
    column needs and the report's list does not.

    ``evidence=False`` drops the evidence lines for a caller under a response
    budget: their first line restates ``file_path``, ``line_number`` and
    ``raw``, and the rest is the resolver's own trace.
    """
    return finding_dict(
        file_path=row.file_path,
        kind=row.kind,
        line_number=row.line_number,
        target=row.target,
        confidence=row.confidence,
        origin=row.origin,
        reason=row.reason,
        raw=row.raw,
        context=row.context,
        evidence=_decode_evidence(row.evidence_json) if evidence else None,
    )


def summarize_confidence_rows(rows: list[DocDriftFinding]) -> dict:
    """Re-derive the high/medium/low buckets on read.

    Calls the same function the analyzer does, so the summary a reader sees
    cannot disagree with the one the pass computed.

    Bucketed on the *rounded* confidence, which is the number every surface
    displays and the one :func:`~repowise.core.analysis.doc_drift.serialize.finding_dict`
    emits. Bucketing the raw value instead would put a stored 0.695 in
    ``medium`` here and in ``high`` on any surface reading the serialized
    dicts, beside a figure both render as 0.70.
    """
    return bucket_confidences(round(r.confidence, 2) for r in rows)
