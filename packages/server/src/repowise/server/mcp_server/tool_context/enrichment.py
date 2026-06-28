"""Optional enrichment blocks for get_context.

Each helper resolves one ``include=`` block (callers/callees, metrics,
community, health) and attaches it to ``result_data`` in place. They are split
out of the main target resolver so the orchestrator and the resolver read as
dispatch rather than one long body.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.signals import file_signals
from repowise.core.persistence.crud import (
    get_all_file_metrics,
    get_community_members,
    get_cross_community_edges,
    get_git_metadata,
    get_graph_edges_for_node,
    get_graph_node,
    get_graph_nodes_by_ids,
    get_node_degree_counts,
)
from repowise.core.persistence.models import (
    CoverageFile,
    GraphEdge,
    GraphNode,
    HealthFileMetric,
    HealthFinding,
    Repository,
)
from repowise.server.mcp_server._helpers import filter_dicts_by_key, filter_path_list

# Minimum confidence for call edges to filter false positives
_MIN_CALL_CONFIDENCE = 0.7

# Edge types that count as a "caller"/"callee" relationship.
_CALL_EDGE_TYPES = ["calls", "extends", "implements"]


async def _count_call_neighbors(
    session: AsyncSession, repo_id: str, node_id: str, *, inbound: bool
) -> int:
    """Count distinct caller (inbound) / callee (outbound) symbols above the
    confidence floor — the TRUE total, independent of the display limit.

    Without this the callers list capped silently at the limit and reported
    ``truncated: false``, which misled an agent doing a find-all-callers sweep
    on a high-fan-in symbol into thinking 20 was the whole set (S2 dogfood).
    """
    matched = GraphEdge.target_node_id if inbound else GraphEdge.source_node_id
    other = GraphEdge.source_node_id if inbound else GraphEdge.target_node_id
    stmt = select(func.count(distinct(other))).where(
        GraphEdge.repository_id == repo_id,
        matched == node_id,
        GraphEdge.edge_type.in_(_CALL_EDGE_TYPES),
        GraphEdge.confidence >= _MIN_CALL_CONFIDENCE,
    )
    return int((await session.execute(stmt)).scalar() or 0)


async def _resolve_call_graph(
    session: AsyncSession,
    repository: Repository,
    target: str,
    target_type: str | None,
    result_data: dict[str, Any],
    *,
    want_callers: bool = False,
    want_callees: bool = False,
    exclude_spec: Any = None,
) -> None:
    """Resolve callers/callees for a symbol and attach to result_data."""
    repo_id = repository.id
    # 99.56% of symbols have <=50 callers (p99=31); the rare hub gets an
    # explicit `*_truncated` + `*_total` signal below rather than a silent cut.
    limit = 50

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
        # File targets get the rolled-up view instead of an empty block —
        # an empty callers list forced a second round-trip per orientation
        # pass for no reason; the graph has the answer at file granularity.
        if node is not None and node.node_type == "file" and want_callers:
            await _resolve_file_level_callers(session, repo_id, node, result_data, exclude_spec)
            if want_callees:
                result_data["callees"] = []
            return
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
        edge_types=_CALL_EDGE_TYPES,
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
            # Definition line of the other symbol — lets the agent jump
            # straight to the call-site neighbourhood instead of grepping
            # for it (the S1 dogfood's most common follow-up).
            "line": other_node.start_line if other_node else None,
            "confidence": e.confidence,
            "edge_type": e.edge_type,
        }
        if is_caller:
            callers.append(entry)
        else:
            callees.append(entry)

    callers = filter_dicts_by_key(callers, "file", exclude_spec)
    callees = filter_dicts_by_key(callees, "file", exclude_spec)

    # Sort by confidence DESC
    callers.sort(key=lambda x: -(x.get("confidence") or 0))
    callees.sort(key=lambda x: -(x.get("confidence") or 0))

    if want_callers:
        result_data["callers"] = callers
        total = await _count_call_neighbors(session, repo_id, node.node_id, inbound=True)
        if total > len(callers):
            result_data["callers_total"] = total
            result_data["callers_truncated"] = True
            result_data["_callers_note"] = (
                f"Showing top {len(callers)} of {total} callers by confidence. The "
                f"graph view caps here; for the complete set (e.g. a signature change) "
                f"grep '{node.name}('."
            )
    if want_callees:
        result_data["callees"] = callees
        total = await _count_call_neighbors(session, repo_id, node.node_id, inbound=False)
        if total > len(callees):
            result_data["callees_total"] = total
            result_data["callees_truncated"] = True


async def _resolve_file_level_callers(
    session: AsyncSession,
    repo_id: str,
    node: GraphNode,
    result_data: dict[str, Any],
    exclude_spec: Any = None,
) -> None:
    """File-target callers: importing files + inbound symbol-call rollup.

    Two granularities merged per caller file: ``imports: true`` when the
    file imports this one, ``inbound_calls`` counting cross-file call edges
    into any symbol defined here.
    """
    from repowise.core.persistence.models import GraphEdge

    # Who imports this file (file-node inbound import edges).
    import_edges = await get_graph_edges_for_node(
        session, repo_id, node.node_id, direction="callers", edge_types=["imports"], limit=50
    )
    importer_ids = [e.source_node_id for e in import_edges if e.target_node_id == node.node_id]
    importer_nodes = await get_graph_nodes_by_ids(session, repo_id, importer_ids)
    # For file nodes the node_id IS the path; file_path may be unset.
    importer_files = {
        (n.file_path or n.node_id)
        for n in importer_nodes.values()
        if n is not None and (n.file_path or n.node_id)
    }

    # Cross-file calls into any symbol defined in this file, rolled up by
    # the calling file.
    target_file = node.file_path or node.node_id
    sym_res = await session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "symbol",
            GraphNode.file_path == target_file,
        )
    )
    sym_ids = [row[0] for row in sym_res.all()]
    calls_by_file: dict[str, int] = {}
    if sym_ids:
        edge_res = await session.execute(
            select(GraphEdge.source_node_id, GraphEdge.confidence).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.target_node_id.in_(sym_ids),
                GraphEdge.edge_type == "calls",
            )
        )
        for src_id, confidence in edge_res.all():
            if (confidence or 0) < _MIN_CALL_CONFIDENCE:
                continue
            src_file = src_id.split("::")[0] if "::" in src_id else src_id
            if src_file == target_file:
                continue  # intra-file calls are not "callers" of the file
            calls_by_file[src_file] = calls_by_file.get(src_file, 0) + 1

    entries: list[dict[str, Any]] = []
    for f in sorted(importer_files | set(calls_by_file)):
        entry: dict[str, Any] = {"file": f}
        if f in importer_files:
            entry["imports"] = True
        if calls_by_file.get(f):
            entry["inbound_calls"] = calls_by_file[f]
        entries.append(entry)
    entries = filter_dicts_by_key(entries, "file", exclude_spec)
    entries.sort(key=lambda x: -(x.get("inbound_calls") or 0))

    result_data["callers"] = entries[:20]
    result_data["_call_graph_note"] = (
        "File-level rollup: importing files plus inbound cross-file call "
        "counts. For symbol-precise callers pass 'file.py::Symbol'."
    )


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
    *,
    exclude_spec: Any = None,
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

    # Process / people / topology signals — the "should I touch this file"
    # context an agent can't get from the score alone. Null fields are dropped
    # so the block stays compact; absent entirely when the file has neither git
    # history nor a graph node.
    git_meta = await get_git_metadata(session, repo_id, file_path)
    node = await get_graph_node(session, repo_id, file_path)
    degrees = (
        await get_node_degree_counts(session, repo_id, file_path) if node is not None else None
    )
    signals = {k: v for k, v in asdict(file_signals(git_meta, degrees)).items() if v is not None}
    if signals:
        health["signals"] = signals

    result_data["health"] = health


async def _resolve_skeleton(
    session: AsyncSession,
    repository: Repository,
    target: str,
    target_type: str | None,
    result_data: dict[str, Any],
    *,
    repo_root: Any = None,
) -> None:
    """Resolve ``include=["skeleton"]`` — a body-elided rendering of one file.

    Slices the on-disk source on the line bounds persisted at index time
    (zero parsing), keeping every signature and the bodies of the
    highest-PageRank symbols under a token budget. File targets only —
    a symbol's "skeleton" is just its signature, which the triage card
    already carries.
    """
    # A "file.py::Symbol" target still has a useful skeleton — the file that
    # defines the symbol. Strip the suffix and skeleton that file rather than
    # failing on a literal path containing "::" (which surfaced as the opaque
    # "Source file could not be read" in the S2 dogfood). Module/other
    # non-file targets with no "::" have no file to render.
    is_symbol_target = "::" in target
    file_target = target.split("::", 1)[0] if is_symbol_target else target
    if not is_symbol_target and target_type != "file":
        result_data["skeleton"] = {"error": "skeleton requires a file target; pass the file path."}
        return
    if not repo_root:
        result_data["skeleton"] = {"error": "MCP server has no repo path configured."}
        return

    from pathlib import Path

    from repowise.core.distill.skeleton import SkeletonSymbol, build_skeleton
    from repowise.core.persistence.models import WikiSymbol

    repo_id = repository.id
    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.file_path == file_target,
        )
    )
    rows = list(res.scalars().all())

    # Symbol-node PageRank is the importance signal for smart body retention.
    pr_res = await session.execute(
        select(GraphNode.name, GraphNode.pagerank).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "symbol",
            GraphNode.file_path == file_target,
        )
    )
    pagerank = {name: pr or 0.0 for name, pr in pr_res.all() if name}

    repo_path = Path(str(repo_root))
    abs_path = (repo_path / file_target).resolve()
    try:
        abs_path.relative_to(repo_path.resolve())
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        result_data["skeleton"] = {
            "error": "Source file could not be read; it may have moved since indexing."
        }
        return

    symbols = [
        SkeletonSymbol(
            name=r.name,
            kind=r.kind,
            start_line=r.start_line,
            end_line=r.end_line,
            signature=r.signature,
            importance=pagerank.get(r.name, 0.0),
        )
        for r in rows
    ]
    result = build_skeleton(
        source,
        symbols,
        mode="smart",
        hotspot=bool(result_data.get("hotspot")),
    )
    result_data["skeleton"] = {
        "mode": result.mode,
        "tokens": result.skeleton_tokens,
        "full_tokens": result.full_tokens,
        "pct_of_full": round(result.pct_of_full, 1),
        "bodies_kept": list(result.bodies_kept),
        "text": result.text,
        # The skeleton is rendered from the live source file read above —
        # its text is fresh by construction. Part of the trust contract:
        # a verified response never needs a follow-up Read.
        "verified": True,
    }
    if is_symbol_target:
        # The caller passed "file.py::Symbol"; tell them this is the whole
        # file's skeleton, and how to get just the symbol's body.
        result_data["skeleton"]["of_file"] = file_target
        result_data["skeleton"]["symbol_hint"] = (
            f"Skeleton of the file defining '{target.split('::', 1)[1]}'. For that "
            f"symbol's full body call get_symbol('{target}')."
        )
    if result.mode == "raw":
        result_data["skeleton"]["note"] = (
            "No usable symbol bounds for this file — returned source as-is."
        )
    elif result.pct_of_full > 40.0:
        # Small files skeletonize poorly: when the skeleton is already a
        # large fraction of the source, tell the agent a Read costs little
        # more and carries everything.
        result_data["skeleton"]["mostly_full"] = True
        result_data["skeleton"]["note"] = (
            f"Skeleton is {round(result.pct_of_full, 1)}% of the full file — "
            "a direct Read costs little more."
        )
