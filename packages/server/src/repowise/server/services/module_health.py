"""Module Health aggregator.

Fetches the rows the per-module rollup needs and folds them with
:mod:`repowise.core.analysis.module_health`, which defines what a module is.
The module value is a rollup bucket key and travels in the URL; for nested
views ``module_path`` is the path prefix the caller passed in.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis import module_health as _fold
from repowise.core.analysis.health.aggregation import module_label
from repowise.core.analysis.module_health import (
    ModuleAccumulator,
    detail_extras,
    module_health_score,
    summarize,
)
from repowise.core.persistence.models import (
    DeadCodeFinding,
    DecisionRecord,
    GitMetadata,
    HealthFileMetric,
    WikiSymbol,
)

#: The one definition behind every ownership rollup. Not a package boundary --
#: see the module docstring.
top_level_module = module_label


def _under_module(file_path: str, module_path: str) -> bool:
    if module_path in ("", "root"):
        return "/" not in file_path
    return file_path == module_path or file_path.startswith(module_path + "/")


async def aggregate_modules(session: AsyncSession, repo_id: str) -> dict[str, ModuleAccumulator]:
    """Fetch the four inputs for *repo_id* and fold them by top-level module."""

    files = (
        (await session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo_id)))
        .scalars()
        .all()
    )
    sym_rows = (
        await session.execute(
            select(WikiSymbol.file_path, WikiSymbol.docstring).where(
                WikiSymbol.repository_id == repo_id
            )
        )
    ).all()
    dead_rows = (
        await session.execute(
            select(DeadCodeFinding.file_path, DeadCodeFinding.lines).where(
                DeadCodeFinding.repository_id == repo_id
            )
        )
    ).all()
    decisions = (
        await session.execute(
            select(DecisionRecord.id, DecisionRecord.affected_modules_json).where(
                DecisionRecord.repository_id == repo_id
            )
        )
    ).all()
    return _fold.aggregate_modules(files, sym_rows, dead_rows, decisions)


def read_repo_health_score(db_path: Path) -> float | None:
    """Return the canonical repo health score from a repo-local wiki.db."""
    if not db_path.exists():
        return None
    # Aggregate in SQL rather than reading every row. A workspace calls this once
    # per repo, and the fetchall() this replaces read ~6,000 rows per repo to
    # produce one number: 61.6ms against 33.2ms across six large repos, and the
    # row count grows with the repo. MAX(CAST(..), 1) reproduces the previous
    # `max(int(nloc or 0), 1)` weight, truncation included; verified identical on
    # 109 real databases.
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT SUM(score * w), SUM(w) FROM ("
                "  SELECT score, MAX(CAST(COALESCE(nloc, 0) AS INTEGER), 1) AS w"
                "  FROM health_file_metrics WHERE score IS NOT NULL"
                ")"
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    except Exception:
        return None

    if not row or not row[1]:
        return None

    weighted_sum, total_weight = row
    average = float(weighted_sum) / float(total_weight)
    return max(0.0, min(100.0, round(average * 10.0, 1)))


async def build_single_file_health(
    session: AsyncSession, repo_id: str, file_path: str
) -> dict | None:
    """Build a ModuleHealthDetail-compatible dict for a single file.

    Returns None when the file has no git_metadata row (i.e. we know
    nothing about it).
    """

    # 1. git_metadata — required for ownership / churn / hotspot
    git_row = (
        await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path == file_path,
            )
        )
    ).scalar_one_or_none()
    if git_row is None:
        return None

    # 2. health_file_metrics — optional
    hfm = (
        await session.execute(
            select(HealthFileMetric.score).where(
                HealthFileMetric.repository_id == repo_id,
                HealthFileMetric.file_path == file_path,
            )
        )
    ).scalar_one_or_none()

    # 3. dead_code_findings for this file
    dead_rows = (
        await session.execute(
            select(
                sa_func.count(DeadCodeFinding.id),
                sa_func.coalesce(sa_func.sum(DeadCodeFinding.lines), 0),
            ).where(
                DeadCodeFinding.repository_id == repo_id,
                DeadCodeFinding.file_path == file_path,
            )
        )
    ).one()
    dead_code_count, dead_code_lines = int(dead_rows[0]), int(dead_rows[1])

    # 4. wiki_symbols for doc coverage
    sym_rows = (
        await session.execute(
            select(
                sa_func.count(WikiSymbol.id),
                sa_func.count(
                    sa_func.nullif(sa_func.trim(WikiSymbol.docstring), "")
                ),
            ).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path == file_path,
            )
        )
    ).one()
    symbol_count, doc_covered = int(sym_rows[0]), int(sym_rows[1])

    # Derive values
    churn_pct = (git_row.churn_percentile or 0.0) * 100.0
    bus_factor = git_row.bus_factor or 0
    is_hotspot = bool(git_row.is_hotspot)
    primary_owner = git_row.primary_owner_name
    primary_owner_pct = git_row.primary_owner_commit_pct or 0.0
    is_silo = primary_owner_pct > 0.8
    doc_coverage_pct = (doc_covered / symbol_count * 100.0) if symbol_count else 0.0

    # Composite health score — use health_file_metrics score (0-10 -> 0-100)
    # when available, otherwise derive from the module formula.
    if hfm is not None:
        health_score = max(0.0, min(100.0, float(hfm) * 10.0))
    else:
        health_score = module_health_score(
            is_silo=is_silo,
            hotspot_fraction=1.0 if is_hotspot else 0.0,
            dead_pct=1.0 if dead_code_count > 0 else 0.0,
            churn_pct=churn_pct,
            doc_pct=doc_coverage_pct / 100.0,
            bus_factor_median=float(bus_factor),
        )

    # Build owners list from top_authors_json
    owners_list: list[dict] = []
    if primary_owner:
        owners_list.append({
            "name": primary_owner,
            "email": git_row.primary_owner_email,
            "file_count": 1,
            "pct": primary_owner_pct,
        })
    try:
        for a in json.loads(git_row.top_authors_json or "[]"):
            name = a.get("name")
            if name and name != primary_owner:
                owners_list.append({
                    "name": name,
                    "email": a.get("email"),
                    "file_count": 1,
                    "pct": a.get("pct", 0.0),
                })
    except json.JSONDecodeError:
        pass

    # Contributors
    contributors: set[str] = set()
    try:
        for a in json.loads(git_row.top_authors_json or "[]"):
            if a.get("name"):
                contributors.add(a["name"])
    except json.JSONDecodeError:
        pass

    return {
        "module_path": file_path,
        "file_count": 1,
        "symbol_count": symbol_count,
        "hotspot_count": 1 if is_hotspot else 0,
        "dead_code_count": dead_code_count,
        "dead_code_lines": dead_code_lines,
        "avg_churn_percentile": churn_pct,
        "median_bus_factor": float(bus_factor),
        "min_bus_factor": bus_factor,
        "primary_owner": primary_owner,
        "primary_owner_pct": primary_owner_pct,
        "is_silo": is_silo,
        "decision_count": 0,
        "doc_coverage_pct": doc_coverage_pct,
        "health_score": health_score,
        # Detail extras
        "owners": owners_list,
        "top_hotspots": [file_path] if is_hotspot else [],
        "governing_decisions": [],
        "contributor_count": len(contributors),
    }


__all__ = [
    "ModuleAccumulator",
    "aggregate_modules",
    "build_single_file_health",
    "detail_extras",
    "read_repo_health_score",
    "summarize",
    "top_level_module",
]
