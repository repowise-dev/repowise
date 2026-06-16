"""/api/repos/{repo_id}/files/{path} — canonical file-detail aggregate.

One call returning everything the file entity page renders: wiki page ref,
health metric + findings + score breakdown, git metadata (significant
commits, commit categories, agent share, co-change partners), coverage
summary + covered lines, owners, graph metrics (pagerank percentile,
in/out degree, dependents/dependencies), per-function blame, governing
decisions, and dead-code findings for the file.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.signals import file_signals
from repowise.core.analysis.health.trends import file_trend
from repowise.core.persistence import crud
from repowise.core.persistence.decision_graph import get_governing_decisions
from repowise.core.persistence.models import DeadCodeFinding, Page, WikiSymbol
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.mcp_server._graph_utils import parse_community_meta, percentile_rank
from repowise.server.routers.code_health import (
    _file_signals_to_dict,
    _file_trend_to_dict,
    _finding_to_dict,
    _metric_to_dict,
    _score_breakdown_from_findings,
)
from repowise.server.routers.git import _hotspot_from_row

router = APIRouter(
    prefix="/api/repos",
    tags=["files"],
    dependencies=[Depends(verify_api_key)],
)


def _json_or(raw: str | None, default: Any) -> Any:
    try:
        parsed = json.loads(raw) if raw else default
    except Exception:
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _blame_to_dict(b: Any) -> dict:
    return {
        "symbol_id": b.symbol_id,
        "function_name": b.function_name,
        "start_line": b.start_line,
        "end_line": b.end_line,
        "line_count": b.line_count,
        "mod_count": b.mod_count,
        "recent_mod_count": b.recent_mod_count,
        "median_author_time": b.median_author_time,
        "owner_name": b.owner_name,
        "owner_email": b.owner_email,
        "owner_line_pct": b.owner_line_pct,
    }


def _symbol_slim(s: WikiSymbol) -> dict:
    return {
        "symbol_id": s.symbol_id,
        "name": s.name,
        "kind": s.kind,
        "signature": s.signature,
        "start_line": s.start_line,
        "end_line": s.end_line,
        "visibility": s.visibility,
        "complexity_estimate": s.complexity_estimate,
        "is_async": s.is_async,
    }


@router.get("/{repo_id}/files/{file_path:path}")
async def file_detail(
    repo_id: str,
    file_path: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Aggregate join over every per-file data source we persist."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    node = await crud.get_graph_node(session, repo_id, file_path)
    git_meta = await crud.get_git_metadata(session, repo_id, file_path)
    metrics = await crud.get_health_metrics(session, repo_id, file_paths=[file_path])
    metric = metrics[0] if metrics else None
    # Degree is read once (graph node only) and shared by the health-signals
    # block below and the graph-context block further down.
    degrees = (
        await crud.get_node_degree_counts(session, repo_id, file_path)
        if node is not None
        else None
    )

    if node is None and git_meta is None and metric is None:
        raise HTTPException(status_code=404, detail=f"File not indexed: {file_path}")

    # --- Wiki page ref ----------------------------------------------------
    page_row = (
        await session.execute(
            select(Page).where(
                Page.repository_id == repo_id,
                Page.page_type == "file_page",
                Page.target_path == file_path,
            )
        )
    ).scalar_one_or_none()
    wiki_page = (
        {
            "id": page_row.id,
            "title": page_row.title,
            "summary": page_row.summary,
            "content": page_row.content,
            "freshness_status": page_row.freshness_status,
            "confidence": page_row.confidence,
            "human_notes": page_row.human_notes,
            "updated_at": page_row.updated_at.isoformat() if page_row.updated_at else None,
        }
        if page_row
        else None
    )

    # --- Health -----------------------------------------------------------
    findings = await crud.get_health_findings(session, repo_id, file_path=file_path)
    snapshots = await crud.list_health_snapshots(session, repo_id)
    health = {
        "metric": _metric_to_dict(metric) if metric else None,
        "breakdown": _score_breakdown_from_findings(findings) if findings else None,
        "findings": [_finding_to_dict(f) for f in findings],
        "trend": _file_trend_to_dict(file_trend(snapshots, file_path)),
        "signals": _file_signals_to_dict(file_signals(git_meta, degrees)),
    }

    # --- Git history ------------------------------------------------------
    git: dict | None = None
    if git_meta is not None:
        git = _hotspot_from_row(git_meta).model_dump(mode="json")
        git["significant_commits"] = _json_or(git_meta.significant_commits_json, [])
        git["top_authors"] = _json_or(git_meta.top_authors_json, [])
        git["co_change_partners"] = _json_or(git_meta.co_change_partners_json, [])
        git["agent"] = {
            "agent_commit_count": git_meta.agent_commit_count or 0,
            "agent_authored_pct": git_meta.agent_authored_pct,
            "tier_counts": _json_or(git_meta.agent_tier_counts_json, {}),
        }
        git["first_commit_at"] = (
            git_meta.first_commit_at.isoformat() if git_meta.first_commit_at else None
        )

    # --- Coverage (incl. line-level set for the heatmap) --------------------
    coverage_rows = await crud.load_coverage_for_repo(session, repo_id, file_paths=[file_path])
    coverage: dict | None = None
    if coverage_rows:
        c = coverage_rows[0]
        coverage = {
            "line_coverage_pct": c.line_coverage_pct,
            "branch_coverage_pct": c.branch_coverage_pct,
            "total_coverable_lines": c.total_coverable_lines,
            "covered_lines": _json_or(c.covered_lines_json, []),
            "source_format": c.source_format,
            "ingested_at": c.ingested_at.isoformat() if c.ingested_at else None,
            "ingested_commit_sha": c.ingested_commit_sha,
        }

    # --- Graph context ------------------------------------------------------
    graph: dict | None = None
    if node is not None:
        all_files = await crud.get_all_file_metrics(session, repo_id)
        assert degrees is not None  # node is not None here, so degrees was loaded
        meta = parse_community_meta(node)
        edges = await crud.get_graph_edges_for_node(
            session, repo_id, file_path, direction="both", limit=40
        )
        neighbor_ids = {
            e.source_node_id if e.source_node_id != file_path else e.target_node_id for e in edges
        }
        node_map = await crud.get_graph_nodes_by_ids(session, repo_id, list(neighbor_ids))
        dependents: list[dict] = []
        dependencies: list[dict] = []
        for e in edges:
            inbound = e.target_node_id == file_path
            other_id = e.source_node_id if inbound else e.target_node_id
            other = node_map.get(other_id)
            entry = {
                "node_id": other_id,
                "node_type": other.node_type if other else "file",
                "language": other.language if other else None,
                "edge_type": e.edge_type,
                "imported_names": _json_or(e.imported_names_json, []),
            }
            (dependents if inbound else dependencies).append(entry)
        graph = {
            "language": node.language,
            "is_entry_point": node.is_entry_point,
            "is_test": node.is_test,
            "symbol_count": node.symbol_count,
            "pagerank": round(node.pagerank or 0.0, 6),
            "pagerank_percentile": percentile_rank(
                node.pagerank or 0.0, [n.pagerank or 0.0 for n in all_files]
            ),
            "in_degree": degrees["in_degree"],
            "out_degree": degrees["out_degree"],
            "community_id": node.community_id,
            "community_label": meta.get("label") or None,
            "dependents": dependents[:20],
            "dependencies": dependencies[:20],
        }

    # --- Symbols + per-function blame ---------------------------------------
    symbol_rows = (
        (
            await session.execute(
                select(WikiSymbol)
                .where(WikiSymbol.repository_id == repo_id, WikiSymbol.file_path == file_path)
                .order_by(WikiSymbol.start_line)
            )
        )
        .scalars()
        .all()
    )
    blame_rows = await crud.get_git_function_blames(session, repo_id, file_path=file_path)

    # --- Governing decisions + dead code -------------------------------------
    governing = [
        {"id": d.id, "title": d.title, "status": d.status}
        for d in await get_governing_decisions(session, repo_id, file_path)
    ]
    dead_rows = (
        (
            await session.execute(
                select(DeadCodeFinding).where(
                    DeadCodeFinding.repository_id == repo_id,
                    DeadCodeFinding.file_path == file_path,
                    DeadCodeFinding.status == "open",
                )
            )
        )
        .scalars()
        .all()
    )
    dead_code = [
        {
            "id": f.id,
            "kind": f.kind,
            "symbol_name": f.symbol_name,
            "confidence": f.confidence,
            "reason": f.reason,
            "lines": f.lines,
            "safe_to_delete": f.safe_to_delete,
        }
        for f in dead_rows
    ]

    return {
        "file_path": file_path,
        "wiki_page": wiki_page,
        "health": health,
        "git": git,
        "coverage": coverage,
        "graph": graph,
        "symbols": [_symbol_slim(s) for s in symbol_rows],
        "function_blame": [_blame_to_dict(b) for b in blame_rows],
        "governing_decisions": governing,
        "dead_code": dead_code,
    }
