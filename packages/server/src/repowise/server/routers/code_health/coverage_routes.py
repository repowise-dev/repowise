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
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Coverage summary + per-file rows.

    Pass ``file_path`` to fetch a single file's full covered-line set.
    Without ``file_path`` we return the summary + a list of per-file
    rows trimmed by ``limit`` (covered_lines arrays stripped).
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    summary = await crud.get_coverage_summary(session, repo_id)
    if summary.get("ingested_at") is not None:
        summary = {**summary, "ingested_at": summary["ingested_at"].isoformat()}
    rows = await crud.load_coverage_for_repo(
        session, repo_id, file_paths=[file_path] if file_path else None
    )
    metrics = await crud.get_health_metrics(session, repo_id)
    metric_by_path = {m.file_path: m for m in metrics}

    if file_path:
        files = [_coverage_row_to_dict(r, include_covered_lines=True) for r in rows]
    else:
        rows_sorted = sorted(rows, key=lambda r: r.line_coverage_pct)
        files = [_coverage_row_to_dict(r) for r in rows_sorted[:limit]]
        # Attach per-file health score so the UI can render a coverage
        # x score matrix without a second request.
        for f in files:
            m = metric_by_path.get(f["file_path"])
            if m is not None:
                f["health_score"] = round(m.score, 2)
                f["nloc"] = m.nloc

    # Aggregate by directory for module-level bars (cheap; one pass).
    modules: dict[str, dict[str, Any]] = {}
    for r in rows:
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
        "modules": module_rows[:limit],
    }
