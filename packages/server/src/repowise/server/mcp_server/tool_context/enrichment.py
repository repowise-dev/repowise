"""Optional enrichment blocks for get_context.

Each helper resolves one ``include=`` block (callers/callees, metrics,
community, health) and attaches it to ``result_data`` in place. They are split
out of the main target resolver so the orchestrator and the resolver read as
dispatch rather than one long body.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.crud import (
    get_all_file_metrics,
    get_community_members,
    get_cross_community_edges,
    get_graph_edges_for_node,
    get_graph_node,
    get_graph_nodes_by_ids,
    get_node_degree_counts,
)
from repowise.core.persistence.models import (
    CoverageFile,
    GraphNode,
    HealthFileMetric,
    HealthFinding,
    Repository,
)

# Minimum confidence for call edges to filter false positives
_MIN_CALL_CONFIDENCE = 0.7


async def _resolve_call_graph(
    session: AsyncSession,
    repository: Repository,
    target: str,
    target_type: str | None,
    result_data: dict[str, Any],
    *,
    want_callers: bool = False,
    want_callees: bool = False,
) -> None:
    """Resolve callers/callees for a symbol and attach to result_data."""
    repo_id = repository.id
    limit = 20

    # Resolve to a graph node (symbol)
    node = await get_graph_node(session, repo_id, target)
    if node is None and "::" in target:
        # Fuzzy: try bare name
        bare_name = target.split("::")[-1]
        res = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_type == "symbol",
                GraphNode.name == bare_name,
            )
        )
        rows = list(res.scalars().all())
        if rows:
            file_hint = target.split("::")[0]
            node = next((r for r in rows if r.file_path == file_hint), rows[0])

    if node is None or node.node_type != "symbol":
        # For file targets, return empty with explanation — callers/callees is symbol-only
        if want_callers:
            result_data["callers"] = []
        if want_callees:
            result_data["callees"] = []
        if node is not None and node.node_type != "symbol":
            result_data["_call_graph_note"] = (
                "callers/callees require a symbol target (function/class/method), "
                f"but '{target}' is a {node.node_type}. Pass a symbol name or file::Symbol."
            )
        return

    direction = "both"
    if want_callers and not want_callees:
        direction = "callers"
    elif want_callees and not want_callers:
        direction = "callees"

    edges = await get_graph_edges_for_node(
        session,
        repo_id,
        node.node_id,
        direction=direction,
        edge_types=["calls", "extends", "implements"],
        limit=limit,
    )

    # Hydrate other nodes
    other_ids = list(
        {e.source_node_id if e.target_node_id == node.node_id else e.target_node_id for e in edges}
    )
    node_map = await get_graph_nodes_by_ids(session, repo_id, other_ids)

    callers: list[dict[str, Any]] = []
    callees: list[dict[str, Any]] = []

    for e in edges:
        # Filter out low-confidence edges (false positives from Tier 3 global resolution)
        if (e.confidence or 0) < _MIN_CALL_CONFIDENCE:
            continue

        is_caller = e.target_node_id == node.node_id
        other_id = e.source_node_id if is_caller else e.target_node_id
        other_node = node_map.get(other_id)

        entry: dict[str, Any] = {
            "symbol_id": other_id,
            "name": other_node.name
            if other_node
            else (other_id.split("::")[-1] if "::" in other_id else other_id),
            "kind": other_node.kind if other_node else None,
            "file": other_node.file_path
            if other_node
            else (other_id.split("::")[0] if "::" in other_id else other_id),
            "confidence": e.confidence,
            "edge_type": e.edge_type,
        }
        if is_caller:
            callers.append(entry)
        else:
            callees.append(entry)

    # Sort by confidence DESC
    callers.sort(key=lambda x: -(x.get("confidence") or 0))
    callees.sort(key=lambda x: -(x.get("confidence") or 0))

    if want_callers:
        result_data["callers"] = callers
    if want_callees:
        result_data["callees"] = callees


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

    degrees = await get_node_degree_counts(session, repo_id, node.node_id)

    # Percentile computation against same-type peers
    all_nodes = await get_all_file_metrics(session, repo_id)
    pr_values = [n.pagerank for n in all_nodes if n.pagerank is not None]
    bt_values = [n.betweenness for n in all_nodes if n.betweenness is not None]

    def _pct(value: float, all_vals: list[float]) -> int:
        if not all_vals:
            return 0
        return round(100 * sum(1 for v in all_vals if v < value) / len(all_vals))

    result_data["metrics"] = {
        "pagerank": round(node.pagerank or 0.0, 6),
        "pagerank_percentile": _pct(node.pagerank or 0.0, pr_values),
        "betweenness": round(node.betweenness or 0.0, 6),
        "betweenness_percentile": _pct(node.betweenness or 0.0, bt_values),
        "in_degree": degrees["in_degree"],
        "out_degree": degrees["out_degree"],
        "community_id": node.community_id,
        "community_label": meta.get("label") or None,
    }


async def _resolve_community(
    session: AsyncSession,
    repository: Repository,
    target: str,
    result_data: dict[str, Any],
) -> None:
    """Resolve community membership and attach to result_data["community"]."""
    repo_id = repository.id

    node = await get_graph_node(session, repo_id, target)
    if node is None or node.community_id is None:
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
    member_paths = [m.node_id for m in members]

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

    Only meaningful for *file* targets. For symbol targets we resolve the
    enclosing file path via the already-computed ``file_path_for_git`` is
    not available here — we fall back to ``target`` when ``target_type``
    is ``"file"`` and otherwise inspect the target string for a
    ``"path::symbol"`` separator.
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
        )
        .order_by(HealthFinding.health_impact.desc())
        .limit(2)
    )
    from repowise.core.analysis.health.suggestions import suggestion_for

    top_biomarkers = [
        {
            "biomarker_type": f.biomarker_type,
            "severity": f.severity,
            "function_name": f.function_name,
            "impact": round(f.health_impact, 2),
            "suggestion": suggestion_for(f.biomarker_type),
        }
        for f in findings_res.scalars().all()
    ]

    coverage_row = (
        await session.execute(
            select(CoverageFile).where(
                CoverageFile.repository_id == repo_id,
                CoverageFile.file_path == file_path,
            )
        )
    ).scalar_one_or_none()

    health: dict[str, Any] = {
        "score": round(metric.score, 2),
        "max_ccn": metric.max_ccn,
        "max_nesting": metric.max_nesting,
        "nloc": metric.nloc,
        "has_test_file": metric.has_test_file,
        "module": metric.module,
        "duplication_pct": metric.duplication_pct,
        "top_biomarkers": top_biomarkers,
    }
    if coverage_row is not None:
        health["coverage"] = {
            "source_format": coverage_row.source_format,
            "line_coverage_pct": coverage_row.line_coverage_pct,
            "branch_coverage_pct": coverage_row.branch_coverage_pct,
            "total_coverable_lines": coverage_row.total_coverable_lines,
        }
    elif metric.line_coverage_pct is not None:
        health["coverage"] = {
            "line_coverage_pct": metric.line_coverage_pct,
            "branch_coverage_pct": metric.branch_coverage_pct,
        }
    result_data["health"] = health
