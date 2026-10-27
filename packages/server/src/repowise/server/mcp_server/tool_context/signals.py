"""get_context ``metrics``, ``community`` and ``health`` blocks."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.communities import has_community
from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.health.complexity.languages import NO_DIALECT_STATUS
from repowise.core.analysis.health.signals import file_signals
from repowise.core.ingestion.models import FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.persistence.crud import (
    coverage_row_dict,
    get_all_file_metrics,
    get_community_members,
    get_cross_community_edges,
    get_git_metadata,
    get_graph_node,
    get_node_degree_counts,
    load_coverage_for_repo,
)
from repowise.core.persistence.crud.analysis.shown import ungated
from repowise.core.persistence.models import HealthFileMetric, HealthFinding, Repository
from repowise.server.mcp_server._helpers import filter_path_list
from repowise.server.mcp_server.tool_context.call_graph import _SYMBOL_USE_EDGE_TYPES

# Degree reported for a FILE: "N files depend on this", counted over file
# dependencies rather than one edge per symbol the file declares.
_FILE_DEPENDENCY_EDGE_TYPES = sorted(FILE_DEPENDENCY_EDGE_TYPES)


async def _resolve_metrics(
    session: AsyncSession,
    repository: Repository,
    target: str,
    result_data: dict[str, Any],
) -> None:
    """Resolve graph importance metrics and attach to result_data["metrics"]."""
    repo_id = repository.id

    node = await get_graph_node(session, repo_id, target)
    if node is None:
        result_data["metrics"] = None
        return

    try:
        meta = json.loads(node.community_meta_json or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}

    # Scoped by layer: a file's neighbours are files, a symbol's are symbols.
    degrees = await get_node_degree_counts(
        session,
        repo_id,
        node.node_id,
        edge_types=(
            # Every use edge type, as `routers/graph/intelligence.py` reports it.
            _SYMBOL_USE_EDGE_TYPES
            if node.node_type == "symbol"
            else _FILE_DEPENDENCY_EDGE_TYPES
        ),
    )

    # Percentile computation against same-type peers
    all_nodes = await get_all_file_metrics(session, repo_id)
    pr_values = [n.pagerank for n in all_nodes if n.pagerank is not None]
    bt_values = [n.betweenness for n in all_nodes if n.betweenness is not None]

    def _pct(value: float, all_vals: list[float]) -> int:
        if not all_vals:
            return 0
        return round(100 * sum(1 for v in all_vals if v < value) / len(all_vals))

    metrics: dict[str, Any] = {
        "pagerank": round(node.pagerank or 0.0, 6),
        "pagerank_percentile": _pct(node.pagerank or 0.0, pr_values),
        "in_degree": degrees["in_degree"],
        "out_degree": degrees["out_degree"],
        "community_id": node.community_id,
        "community_label": meta.get("label") or None,
    }
    # A node added since the last scoring was never measured; its 0.0 default
    # would read as "on no shortest path".
    if node.betweenness_commit is None:
        metrics["betweenness"] = None
        metrics["betweenness_note"] = "not scored yet: added since the last centrality run"
    else:
        metrics["betweenness"] = round(node.betweenness or 0.0, 6)
        metrics["betweenness_percentile"] = _pct(node.betweenness or 0.0, bt_values)
    result_data["metrics"] = metrics


async def _resolve_community(
    session: AsyncSession,
    repository: Repository,
    target: str,
    result_data: dict[str, Any],
    *,
    exclude_spec: Any = None,
) -> None:
    """Resolve community membership and attach to result_data["community"]."""
    repo_id = repository.id

    node = await get_graph_node(session, repo_id, target)
    if node is None or not has_community(node.community_id):
        result_data["community"] = None
        return

    try:
        meta = json.loads(node.community_meta_json or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}

    label = meta.get("label") or f"cluster_{node.community_id}"
    cohesion = float(meta.get("cohesion", 0.0) or 0.0)

    # Get top members (cap at 10 for compact output)
    members = await get_community_members(session, repo_id, node.community_id, limit=10)
    member_paths = filter_path_list([m.node_id for m in members], exclude_spec)

    # Neighboring communities (cap at 5)
    cross_edges = await get_cross_community_edges(session, repo_id, node.community_id)
    neighbors: list[dict[str, Any]] = []
    for ce in cross_edges[:5]:
        nid = ce["target_community_id"]
        nm = await get_community_members(session, repo_id, nid, limit=1)
        nlabel = ""
        if nm:
            try:
                nmeta = json.loads(nm[0].community_meta_json or "{}")
                nlabel = nmeta.get("label", "")
            except (json.JSONDecodeError, TypeError):
                pass
        neighbors.append(
            {
                "id": nid,
                "label": nlabel or f"cluster_{nid}",
                "cross_edges": ce["edge_count"],
            }
        )

    result_data["community"] = {
        "id": node.community_id,
        "label": label,
        "cohesion": round(cohesion, 3),
        "top_members": member_paths,
        "neighbors": neighbors,
    }


async def _resolve_health(
    session: AsyncSession,
    repository: Repository,
    target: str,
    target_type: str,
    result_data: dict[str, Any],
) -> None:
    """Attach per-file health metric + top 2 biomarkers + coverage row.

    A symbol target reads the file before its ``::`` separator.
    """
    file_path: str | None
    if target_type == "file":
        file_path = target
    elif "::" in target:
        file_path = target.split("::", 1)[0]
    else:
        file_path = None

    if not file_path:
        result_data["health"] = None
        return

    repo_id = repository.id
    metric = (
        await session.execute(
            select(HealthFileMetric).where(
                HealthFileMetric.repository_id == repo_id,
                HealthFileMetric.file_path == file_path,
            )
        )
    ).scalar_one_or_none()

    if metric is None:
        result_data["health"] = None
        return

    findings_res = await session.execute(
        select(HealthFinding)
        .where(
            HealthFinding.repository_id == repo_id,
            HealthFinding.file_path == file_path,
            HealthFinding.status == "open",
            HealthFinding.biomarker_type.not_in(excluded_types()),
            ungated("health", HealthFinding.file_path, HealthFinding.biomarker_type),
        )
        .order_by(HealthFinding.health_impact.desc())
        .limit(2)
    )
    from repowise.core.analysis.health.suggestions import suggestion_for

    top_biomarkers = [
        {
            "biomarker_type": f.biomarker_type,
            "severity": f.severity,
            # Absent rather than null on a file-level biomarker, same as the
            # identical field on get_risk's cards.
            **({"function_name": f.function_name} if f.function_name else {}),
            "impact": round(f.health_impact, 2),
            "suggestion": suggestion_for(f.biomarker_type),
        }
        for f in findings_res.scalars().all()
    ]

    coverage_rows = await load_coverage_for_repo(
        session, repo_id, file_paths=[file_path], include_covered_lines=False
    )

    health: dict[str, Any] = {
        "score": round(metric.score, 2) if metric.score is not None else None,
        # Said in words, so a missing score is not read as a missing index.
        **({"analysis_status": NO_DIALECT_STATUS} if metric.score is None else {}),
        "max_ccn": metric.max_ccn,
        "max_nesting": metric.max_nesting,
        "nloc": metric.nloc,
        "has_test_file": metric.has_test_file,
        "module": metric.module,
        "duplication_pct": metric.duplication_pct,
        "top_biomarkers": top_biomarkers,
    }
    if coverage_rows:
        health["coverage"] = coverage_row_dict(coverage_rows[0], include_covered_lines=False)
    elif metric.line_coverage_pct is not None:
        health["coverage"] = {
            "line_coverage_pct": metric.line_coverage_pct,
            "branch_coverage_pct": metric.branch_coverage_pct,
        }

    # Process / people / topology signals. Null fields are dropped; the block is
    # absent when the file has neither git history nor a graph node.
    git_meta = await get_git_metadata(session, repo_id, file_path)
    node = await get_graph_node(session, repo_id, file_path)
    degrees = (
        await get_node_degree_counts(
            session, repo_id, file_path, edge_types=_FILE_DEPENDENCY_EDGE_TYPES
        )
        if node is not None
        else None
    )
    signals = {k: v for k, v in asdict(file_signals(git_meta, degrees)).items() if v is not None}
    if signals:
        health["signals"] = signals

    result_data["health"] = health
