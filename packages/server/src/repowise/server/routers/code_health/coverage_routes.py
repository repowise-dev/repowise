"""Coverage summary + per-file / per-module coverage route."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.server.deps import get_db_session

from ._router import router


def _coverage_row_to_dict(row: Any, *, include_covered_lines: bool = False) -> dict:
    out: dict[str, Any] = {
        "file_path": row.file_path,
        "source_format": row.source_format,
        "line_coverage_pct": row.line_coverage_pct,
        "branch_coverage_pct": row.branch_coverage_pct,
        "total_coverable_lines": row.total_coverable_lines,
        "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
        "ingested_commit_sha": row.ingested_commit_sha,
    }
    if include_covered_lines:
        try:
            out["covered_lines"] = json.loads(row.covered_lines_json or "[]")
        except Exception:
            out["covered_lines"] = []
    return out


@router.get("/api/repos/{repo_id}/health/coverage")
async def health_coverage(
    repo_id: str,
    file_path: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    module_limit: int = Query(1000, ge=0, le=5000),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Coverage summary + per-file rows.

    Pass ``file_path`` to fetch a single file's full covered-line set.
    Without ``file_path`` we return the summary + a list of per-file
    rows trimmed by ``limit`` (covered_lines arrays stripped).

    ``limit`` caps ``files``; ``module_limit`` caps ``modules``. Separate
    parameters because they bound unrelated things — one is a page of files, the
    other a rollup as long as the repo's covered-directory count — and one
    ``limit`` governing both meant a caller asking for a single file row was
    told the repo had a single module.

    ``modules_total`` is always the full count, so a trimmed rollup can never be
    read as the whole repo. ``module_limit=0`` returns none of them, for callers
    that want the summary and nothing else.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # One narrow read of the whole table, shared by all three blocks below.
    # ``summary`` and ``modules`` are both repo-wide aggregates, so this cannot
    # be scoped by ``file_path`` or trimmed by ``limit`` — and none of the three
    # reads ``covered_lines_json``, which is most of the table's bytes.
    all_rows = await crud.load_coverage_for_repo(
        session, repo_id, include_covered_lines=False
    )
    summary = await crud.get_coverage_summary(session, repo_id, rows=all_rows)
    if summary.get("ingested_at") is not None:
        summary = {**summary, "ingested_at": summary["ingested_at"].isoformat()}

    if file_path:
        # The one caller that wants the covered-line set, for one file.
        detail = await crud.load_coverage_for_repo(
            session, repo_id, file_paths=[file_path], include_covered_lines=True
        )
        files = [_coverage_row_to_dict(r, include_covered_lines=True) for r in detail]
    else:
        rows_sorted = sorted(all_rows, key=lambda r: r.line_coverage_pct)
        files = [_coverage_row_to_dict(r) for r in rows_sorted[:limit]]
        # Attach per-file health score so the UI can render a coverage
        # x score matrix without a second request. Scoped to the rows we are
        # actually returning: a repo-wide read hydrates every metric row and
        # pays for the grouped deduction aggregate behind its worst-first
        # ordering, neither of which this join uses.
        metrics = await crud.get_health_metrics(
            session, repo_id, file_paths=[f["file_path"] for f in files]
        )
        metric_by_path = {m.file_path: m for m in metrics}
        for f in files:
            m = metric_by_path.get(f["file_path"])
            if m is not None:
                f["health_score"] = round(m.score, 2)
                f["nloc"] = m.nloc

    # Aggregate by directory for module-level bars (cheap; one pass).
    # Always over the repo-wide read, never over ``files``: this is what the
    # repo's coverage looks like by directory, not what this page of it does.
    modules: dict[str, dict[str, Any]] = {}
    for r in all_rows:
        mod = r.file_path.rsplit("/", 1)[0] if "/" in r.file_path else "(root)"
        bucket = modules.setdefault(mod, {"covered": 0, "total": 0, "files": 0})
        bucket["files"] += 1
        bucket["total"] += r.total_coverable_lines
        bucket["covered"] += round(r.line_coverage_pct / 100.0 * r.total_coverable_lines)
    module_rows = [
        {
            "module": name,
            "files": v["files"],
            "covered_lines": v["covered"],
            "total_lines": v["total"],
            "line_coverage_pct": (
                round(v["covered"] / v["total"] * 100.0, 2) if v["total"] else 0.0
            ),
        }
        for name, v in modules.items()
    ]
    module_rows.sort(key=lambda x: x["line_coverage_pct"])

    return {
        "summary": summary,
        "files": files,
        "modules": module_rows[:module_limit] if module_limit else [],
        "modules_total": len(module_rows),
    }
