"""/api/repos/{repo_id}/stats/highlights: the "By the Numbers" payload.

A read-only aggregate for the repo Stats page. The derivations live in
:mod:`repowise.core.stats_highlights` so the hosted backend can build the same
payload from its artifacts; this module only reads rows. Every read is a narrow
column select or a capped ordered query, and nothing recomputes analysis.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core import stats_highlights as sh
from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    GitCommit,
    GitFunctionBlame,
    GitMetadata,
    GraphMetric,
    GraphNode,
    GraphNodeMembership,
    HealthFileMetric,
    WikiSymbol,
)
from repowise.server.deps import get_db_session, verify_api_key

router = APIRouter(
    prefix="/api/repos",
    tags=["stats"],
    dependencies=[Depends(verify_api_key)],
)

# Blame rows read for the most-rewritten award. Ordered by the indexed
# mod_count, so this only needs headroom for test functions to be skipped.
_BLAME_CANDIDATES = 50


async def _rows(session: AsyncSession, stmt: Any) -> list[Any]:
    return list((await session.execute(stmt)).mappings().all())


async def _largest_cycle(session: AsyncSession, repo_id: str) -> dict[str, Any] | None:
    """Largest import cycle, off the materialized SCC membership rows."""
    sizes = (
        await session.execute(
            select(func.count())
            .where(
                GraphNodeMembership.repository_id == repo_id,
                GraphNodeMembership.scc_size > 1,
            )
            .group_by(GraphNodeMembership.scc_id)
        )
    ).all()
    if not sizes:
        return None
    return {"files": max(int(n) for (n,) in sizes), "cycle_count": len(sizes)}


async def _most_central(session: AsyncSession, repo_id: str) -> dict[str, Any] | None:
    candidates = await _rows(
        session,
        select(
            GraphMetric.node_id.label("path"),
            GraphMetric.in_degree,
            GraphMetric.pagerank,
        )
        .join(
            GraphNode,
            (GraphNode.repository_id == GraphMetric.repository_id)
            & (GraphNode.node_id == GraphMetric.node_id),
        )
        .where(
            GraphMetric.repository_id == repo_id,
            GraphNode.node_type == "file",
            GraphNode.is_test.is_(False),
            GraphNode.external_system_id.is_(None),
            ~GraphNode.node_id.like("external:%"),
        )
        .order_by(GraphMetric.in_degree.desc())
        .limit(10),
    )
    hit = sh.most_imported_file(candidates)
    if hit is not None:
        return hit
    # Graph metrics not materialized: fall back to the PageRank pick.
    central = (
        await session.execute(
            select(GraphNode.node_id, GraphNode.pagerank)
            .where(GraphNode.repository_id == repo_id, GraphNode.node_type == "file")
            .order_by(GraphNode.pagerank.desc())
            .limit(1)
        )
    ).first()
    if central is None or not (central[1] or 0) > 0:
        return None
    return {"path": central[0], "pagerank": round(float(central[1]), 4)}


async def _symbol_records(
    session: AsyncSession, repo_id: str, is_test: Any
) -> dict[str, Any]:
    """Function, symbol-shape and function-blame records."""
    functions = await _rows(
        session,
        select(
            WikiSymbol.name,
            WikiSymbol.kind,
            WikiSymbol.file_path,
            WikiSymbol.start_line,
            WikiSymbol.end_line,
        ).where(WikiSymbol.repository_id == repo_id, WikiSymbol.kind.in_(("function", "method"))),
    )
    shape = (
        await session.execute(
            select(
                func.count(WikiSymbol.id),
                func.sum(func.cast(WikiSymbol.is_async, Integer)),
                func.count(WikiSymbol.docstring),
            ).where(WikiSymbol.repository_id == repo_id)
        )
    ).one()
    complex_sym = (
        await session.execute(
            select(WikiSymbol.name, WikiSymbol.file_path, WikiSymbol.complexity_estimate)
            .where(WikiSymbol.repository_id == repo_id)
            .order_by(WikiSymbol.complexity_estimate.desc())
            .limit(1)
        )
    ).first()
    blame = await _rows(
        session,
        select(GitFunctionBlame.function_name, GitFunctionBlame.file_path, GitFunctionBlame.mod_count)
        .where(GitFunctionBlame.repository_id == repo_id)
        .order_by(GitFunctionBlame.mod_count.desc())
        .limit(_BLAME_CANDIDATES),
    )
    out = sh.build_function_records(functions, is_test)
    optional = {
        "most_complex_symbol": (
            {"name": complex_sym[0], "file_path": complex_sym[1], "complexity": int(complex_sym[2])}
            if complex_sym is not None and (complex_sym[2] or 0) > 0
            else None
        ),
        "symbol_shape": sh.symbol_shape(int(shape[0] or 0), int(shape[1] or 0), int(shape[2] or 0)),
        "most_patched_function": sh.most_patched_function(blame, is_test),
    }
    out.update({k: v for k, v in optional.items() if v})
    return out


@router.get("/{repo_id}/stats/highlights")
async def stats_highlights(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Everything the Stats page needs, in one call."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    totals = {
        "total_commit_count": repo.total_commit_count,
        "first_commit_at": repo.first_commit_at,
        "total_contributor_count": repo.total_contributor_count,
        "first_commit_author": repo.first_commit_author,
        "first_commit_subject": repo.first_commit_subject,
        "total_lines_added": repo.total_lines_added,
        "total_lines_deleted": repo.total_lines_deleted,
    }
    file_nodes = await _rows(
        session,
        select(
            GraphNode.node_id,
            GraphNode.language,
            GraphNode.symbol_count,
            GraphNode.external_system_id,
        ).where(GraphNode.repository_id == repo_id, GraphNode.node_type == "file"),
    )
    metrics = await _rows(
        session,
        select(
            HealthFileMetric.file_path,
            HealthFileMetric.nloc,
            HealthFileMetric.module,
            HealthFileMetric.max_ccn,
            HealthFileMetric.is_test,
        ).where(HealthFileMetric.repository_id == repo_id),
    )
    all_meta = await _rows(
        session,
        select(
            GitMetadata.file_path,
            GitMetadata.primary_owner_name,
            GitMetadata.bus_factor,
            GitMetadata.commit_count_total,
            GitMetadata.commit_count_capped,
            GitMetadata.first_commit_at,
            GitMetadata.last_commit_at,
        ).where(GitMetadata.repository_id == repo_id),
    )
    commits = await _rows(
        session,
        select(
            GitCommit.committed_at,
            GitCommit.committed_offset_minutes,
            GitCommit.author_name,
            GitCommit.author_email,
            GitCommit.sha,
            GitCommit.subject,
            GitCommit.lines_added,
            GitCommit.lines_deleted,
            GitCommit.files_changed,
        ).where(GitCommit.repository_id == repo_id),
    )

    activity = sh.build_commit_pass(commits, totals)
    records = {
        **sh.build_file_records(metrics, all_meta, totals["first_commit_at"]),
        **await _symbol_records(session, repo_id, sh.is_test_checker(metrics)),
    }
    optional = {
        "most_central_file": await _most_central(session, repo_id),
        "largest_cycle": await _largest_cycle(session, repo_id),
        "biggest_commit": activity.pop("biggest_commit"),
        "widest_commit": activity.pop("widest_commit"),
        "biggest_purge": activity.pop("biggest_purge"),
    }
    records.update({k: v for k, v in optional.items() if v})

    rhythm = activity["rhythm"]
    rhythm["code_half_life_days"] = sh.code_half_life(all_meta, activity["origin"]["last_commit_at"])
    people = {
        **sh.build_people(all_meta),
        "contributor_count": activity["origin"]["contributor_count"],
        "chronotypes": activity["chronotypes"],
        "arrivals": activity["arrivals"],
    }
    return {
        "repo": {"id": repo.id, "name": repo.name},
        "scale": sh.build_scale(file_nodes, metrics),
        "origin": activity["origin"],
        "churn": sh.build_churn(totals),
        "rhythm": rhythm,
        "people": people,
        "records": records,
    }
