"""CRUD operations for coverage files (repowise persistence layer)."""

from __future__ import annotations

import json
from typing import Any, Literal, overload

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
    mapping_partial: bool = False,
) -> None:
    """Replace coverage rows for *repository_id* with *files*.

    Mirrors the delete-then-insert pattern used by the health writers.
    *files* is a list of ``FileCoverage`` dataclasses (or dicts with the
    same shape). ``mapping_partial`` marks the whole ingest as a fragment
    (fewer than half the report's files mapped to the repo tree, #1746);
    it is a property of the ingest, not of any one row.
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
                    mapping_partial=mapping_partial,
                    **{
                        k: v
                        for k, v in data.items()
                        if k
                        not in (
                            "id",
                            "repository_id",
                            "source_format",
                            "ingested_commit_sha",
                            "mapping_partial",
                        )
                        and hasattr(CoverageFile, k)
                    },
                )
            )
        await session.flush()


#: Every column of ``CoverageFile`` except ``covered_lines_json``. That blob is
#: the per-file set of covered line numbers, and it dominates the table: 467 KB
#: of the 549 KB stored for this repo's 1,401 rows. Only the single-file detail
#: view reads it, so every repo-wide caller was hydrating it to throw it away.
_COVERAGE_SCALAR_COLUMNS = (
    CoverageFile.file_path,
    CoverageFile.source_format,
    CoverageFile.line_coverage_pct,
    CoverageFile.branch_coverage_pct,
    CoverageFile.total_coverable_lines,
    CoverageFile.mapping_partial,
    CoverageFile.ingested_at,
    CoverageFile.ingested_commit_sha,
)


@overload
async def load_coverage_for_repo(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = ...,
    include_covered_lines: Literal[True] = ...,
) -> list[CoverageFile]: ...


@overload
async def load_coverage_for_repo(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = ...,
    include_covered_lines: Literal[False],
) -> list[Any]: ...


async def load_coverage_for_repo(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = None,
    include_covered_lines: bool = True,
) -> list[Any]:
    """Coverage rows for a repo, optionally scoped to *file_paths*.

    ``include_covered_lines=False`` returns ``Row`` objects carrying every
    column except ``covered_lines_json``. They are attribute-accessed exactly
    like the ORM entities, so a caller that reads named fields needs no change
    — but a caller that touches ``covered_lines_json`` must ask for it.
    """
    q = (
        select(CoverageFile)
        if include_covered_lines
        else select(*_COVERAGE_SCALAR_COLUMNS)
    ).where(CoverageFile.repository_id == repository_id)
    if file_paths is not None:
        q = q.where(CoverageFile.file_path.in_(file_paths))
    result = await session.execute(q)
    if include_covered_lines:
        return list(result.scalars().all())
    return list(result.all())


async def get_coverage_summary(
    session: AsyncSession,
    repository_id: str,
    *,
    rows: list[Any] | None = None,
) -> dict[str, Any]:
    """Repo-level coverage aggregate. Returns an empty shape when no rows.

    *rows* lets a caller that has already loaded the coverage table hand it
    over instead of paying for a second full read. It must be exactly what this
    function would have read itself — ``load_coverage_for_repo(repository_id)``,
    i.e. **every** row for the repo. Handing over a subset (a ``file_paths=``
    read, say) would silently report that subset's coverage as the repo's.
    """
    if rows is None:
        rows = await load_coverage_for_repo(
            session, repository_id, include_covered_lines=False
        )
    if not rows:
        return {
            "file_count": 0,
            "covered_lines": 0,
            "total_lines": 0,
            "line_coverage_pct": None,
            "branch_coverage_pct": None,
            "source_format": None,
            "mapping_partial": None,
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
    # A partial ingest is a whole-table property: every row of the latest
    # delete-then-insert batch carries the same flag, so any row reports the
    # table's. Rows written before the flag existed read False (column
    # default), which is correct for complete legacy ingests.
    mapping_partial = bool(getattr(latest, "mapping_partial", False))
    return {
        "file_count": len(rows),
        "covered_lines": covered,
        "total_lines": total,
        "line_coverage_pct": round(line_pct, 2),
        "branch_coverage_pct": round(branch_pct, 2) if branch_pct is not None else None,
        "source_format": latest.source_format,
        "mapping_partial": mapping_partial,
        "ingested_at": latest.ingested_at,
        "ingested_commit_sha": latest.ingested_commit_sha,
    }
