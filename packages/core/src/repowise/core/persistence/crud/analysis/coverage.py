"""CRUD operations for coverage files (repowise persistence layer)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import CoverageFile, _new_uuid
from .._shared import _BATCH_SIZE


async def save_coverage_files(
    session: AsyncSession,
    repository_id: str,
    files: list[Any],
    *,
    source_format: str,
    ingested_commit_sha: str | None = None,
) -> None:
    """Replace coverage rows for *repository_id* with *files*.

    Mirrors the delete-then-insert pattern used by the health writers.
    *files* is a list of ``FileCoverage`` dataclasses (or dicts with the
    same shape).
    """
    existing = await session.execute(
        select(CoverageFile).where(CoverageFile.repository_id == repository_id)
    )
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()

    for i in range(0, len(files), _BATCH_SIZE):
        batch = files[i : i + _BATCH_SIZE]
        for f in batch:
            if hasattr(f, "file_path"):
                data = {
                    "file_path": f.file_path,
                    "line_coverage_pct": float(f.line_coverage_pct),
                    "branch_coverage_pct": (
                        float(f.branch_coverage_pct) if f.branch_coverage_pct is not None else None
                    ),
                    "covered_lines_json": json.dumps(list(f.covered_lines or [])),
                    "total_coverable_lines": int(f.total_coverable_lines or 0),
                }
            else:
                data = dict(f)
                if "covered_lines" in data:
                    data["covered_lines_json"] = json.dumps(list(data.pop("covered_lines") or []))

            session.add(
                CoverageFile(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    source_format=source_format,
                    ingested_commit_sha=ingested_commit_sha,
                    **{
                        k: v
                        for k, v in data.items()
                        if k
                        not in (
                            "id",
                            "repository_id",
                            "source_format",
                            "ingested_commit_sha",
                        )
                        and hasattr(CoverageFile, k)
                    },
                )
            )
        await session.flush()


async def load_coverage_for_repo(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = None,
) -> list[CoverageFile]:
    q = select(CoverageFile).where(CoverageFile.repository_id == repository_id)
    if file_paths is not None:
        q = q.where(CoverageFile.file_path.in_(file_paths))
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_coverage_summary(
    session: AsyncSession,
    repository_id: str,
) -> dict[str, Any]:
    """Repo-level coverage aggregate. Returns an empty shape when no rows."""
    rows = await load_coverage_for_repo(session, repository_id)
    if not rows:
        return {
            "file_count": 0,
            "covered_lines": 0,
            "total_lines": 0,
            "line_coverage_pct": None,
            "branch_coverage_pct": None,
            "source_format": None,
            "ingested_at": None,
            "ingested_commit_sha": None,
        }
    covered = 0
    total = 0
    branch_pcts: list[float] = []
    branch_weights: list[int] = []
    for r in rows:
        covered += round(r.line_coverage_pct / 100.0 * r.total_coverable_lines)
        total += r.total_coverable_lines
        if r.branch_coverage_pct is not None:
            branch_pcts.append(r.branch_coverage_pct)
            branch_weights.append(max(r.total_coverable_lines, 1))
    line_pct = (covered / total * 100.0) if total else 0.0
    branch_pct: float | None
    if branch_pcts:
        wsum = sum(branch_weights)
        branch_pct = sum(p * w for p, w in zip(branch_pcts, branch_weights, strict=True)) / wsum
    else:
        branch_pct = None
    latest = max(rows, key=lambda r: r.ingested_at)
    return {
        "file_count": len(rows),
        "covered_lines": covered,
        "total_lines": total,
        "line_coverage_pct": round(line_pct, 2),
        "branch_coverage_pct": round(branch_pct, 2) if branch_pct is not None else None,
        "source_format": latest.source_format,
        "ingested_at": latest.ingested_at,
        "ingested_commit_sha": latest.ingested_commit_sha,
    }
