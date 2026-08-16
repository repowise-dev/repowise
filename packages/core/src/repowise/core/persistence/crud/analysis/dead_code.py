"""CRUD operations for dead-code findings (repowise persistence layer)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.dead_code.risk_factors import (
    RISK_CAP_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
    effective_safe_to_delete,
)

from ...models import DeadCodeFinding, _new_uuid
from .._shared import _BATCH_SIZE, _finding_file_path


def _dead_code_row_kwargs(finding: Any, repository_id: str) -> dict:
    """Normalize a DeadCodeFindingData-like object or plain dict into kwargs
    for the ``DeadCodeFinding`` ORM row."""
    if hasattr(finding, "kind"):
        data = {
            "kind": str(finding.kind.value)
            if hasattr(finding.kind, "value")
            else str(finding.kind),
            "file_path": finding.file_path,
            "symbol_name": finding.symbol_name,
            "symbol_kind": finding.symbol_kind,
            "confidence": finding.confidence,
            "reason": finding.reason,
            "last_commit_at": finding.last_commit_at,
            "commit_count_90d": finding.commit_count_90d,
            "lines": finding.lines,
            "start_line": finding.start_line,
            "end_line": finding.end_line,
            "package": finding.package,
            "evidence_json": json.dumps(finding.evidence if hasattr(finding, "evidence") else []),
            "safe_to_delete": finding.safe_to_delete,
            "primary_owner": finding.primary_owner,
            "age_days": finding.age_days,
        }
    else:
        data = dict(finding)
        if "evidence" in data:
            data["evidence_json"] = json.dumps(data.pop("evidence"))

    return {
        "id": _new_uuid(),
        "repository_id": repository_id,
        **{
            k: v
            for k, v in data.items()
            if k not in ("id", "repository_id") and hasattr(DeadCodeFinding, k)
        },
    }


async def save_dead_code_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[dict],
) -> None:
    """Persist dead code findings, replacing any existing open findings for the repo."""
    # Delete existing open findings for this repo before saving new ones
    existing = await session.execute(
        select(DeadCodeFinding).where(
            DeadCodeFinding.repository_id == repository_id,
            DeadCodeFinding.status == "open",
        )
    )
    for row in existing.scalars().all():
        await session.delete(row)

    for i in range(0, len(findings), _BATCH_SIZE):
        batch = findings[i : i + _BATCH_SIZE]
        for finding in batch:
            session.add(DeadCodeFinding(**_dead_code_row_kwargs(finding, repository_id)))
        await session.flush()


def _finding_identity(finding: Any) -> tuple:
    """The (file, kind, symbol) triple that makes two findings the same finding.

    Compared against ``DeadCodeFinding`` rows, whose ``kind`` column holds the
    enum's *value*. Both shapes reach here: the dataclass (workspace path) and
    ``dataclasses.asdict`` output (CLI path), and ``asdict`` leaves the
    ``DeadCodeKind`` member intact rather than converting it. ``DeadCodeKind``
    is a ``StrEnum``, so ``str()`` happens to give the value today; unwrapping
    ``.value`` states the requirement instead of inheriting it from the mixin.
    """
    kind = finding.kind if hasattr(finding, "kind") else finding.get("kind", "")
    symbol = finding.symbol_name if hasattr(finding, "kind") else finding.get("symbol_name")
    return (_finding_file_path(finding), str(getattr(kind, "value", kind)), symbol)


async def replace_dead_code_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
    *,
    scope: frozenset[str] | set[str] | None = None,
) -> None:
    """Replace open dead-code findings, for *scope* or for the whole repository.

    The incremental update path used to replace findings only for the files
    that changed, which meant an unchanged file kept whatever verdict the last
    full index gave it. Dead code is a cross-file property — removing the last
    import of a module makes *that module* dead, and it is not in the change
    set — so a change-scoped write can never express the result of the
    analysis that produced it. The analyzer computes the repo-wide truth
    either way; this persists it rather than discarding the inconvenient part.

    *scope* is the set of file paths the caller can actually speak for, and
    ``None`` means all of them. It is not a performance knob and not a
    threshold: dead-code confidence is scored from per-file git metadata, and
    a file the caller has no metadata for scores 0.7 with
    ``safe_to_delete=True`` however actively it is committed to. Writing that
    would be worse than writing nothing, so a caller holding partial metadata
    passes the part it has and every other file keeps its stored verdict.
    Rows outside *scope* are neither deleted nor inserted.

    Findings the user has acted on are not resurrected. The delete is scoped
    to ``status == "open"``, so a dismissed or resolved row survives it, and
    any incoming finding matching such a row by (file, kind, symbol) is
    dropped rather than re-inserted as a fresh ``open`` duplicate. Without
    that second half a repo-wide write would re-open every dismissal on every
    update, which the old change-scoped write only did for changed files.

    There is no unique constraint on that triple (and ``symbol_name`` is
    nullable, so one would not bite without a functional index), hence
    delete-then-insert with the surviving keys filtered out in Python rather
    than an ``ON CONFLICT`` upsert.
    """
    existing = await session.execute(
        select(DeadCodeFinding).where(DeadCodeFinding.repository_id == repository_id)
    )
    acted_on: set[tuple] = set()
    for row in existing.scalars().all():
        if scope is not None and row.file_path not in scope:
            continue
        if row.status == "open":
            await session.delete(row)
        else:
            acted_on.add((row.file_path, row.kind, row.symbol_name))
    await session.flush()

    writable = [f for f in findings if scope is None or _finding_file_path(f) in scope]
    for i in range(0, len(writable), _BATCH_SIZE):
        batch = writable[i : i + _BATCH_SIZE]
        for finding in batch:
            if _finding_identity(finding) in acted_on:
                continue
            session.add(DeadCodeFinding(**_dead_code_row_kwargs(finding, repository_id)))
        await session.flush()


async def get_dead_code_findings(
    session: AsyncSession,
    repository_id: str,
    *,
    kind: str | None = None,
    min_confidence: float = 0.0,
    status: str = "open",
) -> list[DeadCodeFinding]:
    """Return dead code findings filtered by kind, confidence, and status."""
    q = select(DeadCodeFinding).where(
        DeadCodeFinding.repository_id == repository_id,
        DeadCodeFinding.status == status,
        DeadCodeFinding.confidence >= min_confidence,
    )
    if kind is not None:
        q = q.where(DeadCodeFinding.kind == kind)
    q = q.order_by(DeadCodeFinding.confidence.desc())
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_dead_code_status(
    session: AsyncSession,
    finding_id: str,
    status: str,
    note: str | None = None,
) -> DeadCodeFinding | None:
    """Update the status (and optional note) of a dead code finding."""
    finding = await session.get(DeadCodeFinding, finding_id)
    if finding is None:
        return None
    finding.status = status
    if note is not None:
        finding.note = note
    await session.flush()
    return finding


async def get_dead_code_summary(session: AsyncSession, repository_id: str) -> dict:
    """Return aggregate dead code statistics."""
    result = await session.execute(
        select(DeadCodeFinding).where(
            DeadCodeFinding.repository_id == repository_id,
            DeadCodeFinding.status == "open",
        )
    )
    findings = list(result.scalars().all())

    summary: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    total_lines = 0
    by_kind: dict[str, int] = {}

    for f in findings:
        if f.confidence >= SAFE_CONFIDENCE_THRESHOLD:
            summary["high"] += 1
        elif f.confidence >= RISK_CAP_CONFIDENCE:
            summary["medium"] += 1
        else:
            summary["low"] += 1
        total_lines += f.lines
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1

    # Re-derive effective safety from confidence + path risk factors rather
    # than trusting the persisted boolean alone, so findings written before the
    # risk-factor logic existed (or in a config/bootstrap/database/environment
    # file the allowlist missed) are not counted as deletion-ready.
    deletable_lines = sum(
        f.lines
        for f in findings
        if effective_safe_to_delete(f.confidence, f.file_path, f.safe_to_delete)
    )

    return {
        "total_findings": len(findings),
        "confidence_summary": summary,
        "deletable_lines": deletable_lines,
        "total_lines": total_lines,
        "by_kind": by_kind,
    }
