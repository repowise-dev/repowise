"""CRUD operations for dead-code findings (repowise persistence layer)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.dead_code.risk_factors import effective_safe_to_delete

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


async def upsert_dead_code_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
    *,
    file_paths: list[str],
) -> None:
    """Replace open dead-code findings **only for the given file paths**.

    Used by the incremental ``repowise update`` path so unchanged files keep
    their findings instead of being wiped on every partial re-index. Callers
    must pass the full set of *changed* file paths (not just paths that
    produced findings) so a changed-but-now-clean file has its stale findings
    removed.
    """
    if not file_paths:
        return
    allowed = set(file_paths)
    existing = await session.execute(
        select(DeadCodeFinding).where(
            DeadCodeFinding.repository_id == repository_id,
            DeadCodeFinding.status == "open",
            DeadCodeFinding.file_path.in_(file_paths),
        )
    )
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()

    # Insert only within the replaced scope (delete is scoped to file_paths).
    scoped = [f for f in findings if _finding_file_path(f) in allowed]
    for i in range(0, len(scoped), _BATCH_SIZE):
        batch = scoped[i : i + _BATCH_SIZE]
        for finding in batch:
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
        if f.confidence >= 0.7:
            summary["high"] += 1
        elif f.confidence >= 0.4:
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
