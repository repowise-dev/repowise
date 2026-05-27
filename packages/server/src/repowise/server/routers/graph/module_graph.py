"""Collapsed directory-level (module) graph view."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GraphEdge, GraphNode, Page
from repowise.server.deps import get_db_session
from repowise.server.routers.graph._common import with_repo
from repowise.server.routers.graph.signals import _EMPTY_SIGNALS, _collect_node_signals
from repowise.server.schemas import (
    ModuleEdgeResponse,
    ModuleGraphResponse,
    ModuleNodeResponse,
)

router = APIRouter()


@router.get("/{repo_id}/modules", response_model=ModuleGraphResponse)
async def module_graph(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> ModuleGraphResponse:
    """Collapsed directory-level graph: one node per top-level path segment."""
    node_result = await session.execute(select(GraphNode).where(GraphNode.repository_id == repo_id))
    nodes = node_result.scalars().all()

    # Group nodes by first path segment, detecting monorepo roots
    first_level: dict[str, list[GraphNode]] = {}
    for n in nodes:
        parts = n.node_id.split("/")
        seg = parts[0] if len(parts) > 1 else n.node_id
        first_level.setdefault(seg, []).append(n)

    total = len(nodes) or 1
    monorepo_roots: set[str] = set()
    for seg, seg_nodes in first_level.items():
        # When a single top-level directory contains >70% of all files, treat it
        # as a monorepo root and group by the *second* path segment instead.
        # Threshold chosen empirically: catches src/-heavy repos without
        # splitting balanced monorepos like packages/a + packages/b (each ~50%).
        if len(seg_nodes) / total > 0.70:
            monorepo_roots.add(seg)

    modules: dict[str, list[GraphNode]] = {}
    for n in nodes:
        parts = n.node_id.split("/")
        if len(parts) <= 1:
            module = n.node_id
        elif parts[0] in monorepo_roots and len(parts) > 2:
            module = parts[0] + "/" + parts[1]
        else:
            module = parts[0]
        modules.setdefault(module, []).append(n)

    # Fetch page confidence for doc coverage
    page_result = await session.execute(
        select(Page.target_path, Page.confidence).where(Page.repository_id == repo_id)
    )
    page_coverage: dict[str, float] = {row.target_path: row.confidence for row in page_result.all()}

    # Collect signals once for all file nodes; aggregate per module below.
    all_node_ids = [n.node_id for n in nodes]
    signals = await _collect_node_signals(session, repo_id, all_node_ids)

    node_to_module: dict[str, str] = {}
    module_nodes: list[ModuleNodeResponse] = []
    for module_id, module_file_nodes in modules.items():
        file_count = len(module_file_nodes)
        symbol_count = sum(n.symbol_count for n in module_file_nodes)
        avg_pagerank = sum(n.pagerank for n in module_file_nodes) / max(file_count, 1)
        covered = sum(1 for n in module_file_nodes if page_coverage.get(n.node_id, 0.0) >= 0.7)
        doc_coverage_pct = covered / max(file_count, 1)

        hotspot_count = 0
        dead_count = 0
        has_decision = False
        owner_tally: dict[str, int] = {}
        for n in module_file_nodes:
            sig = signals.get(n.node_id, _EMPTY_SIGNALS)
            if sig.is_hotspot:
                hotspot_count += 1
            if sig.is_dead:
                dead_count += 1
            if sig.has_decision:
                has_decision = True
            if sig.primary_owner:
                owner_tally[sig.primary_owner] = owner_tally.get(sig.primary_owner, 0) + 1
        primary_owner = max(owner_tally, key=owner_tally.get) if owner_tally else None

        for n in module_file_nodes:
            node_to_module[n.node_id] = module_id

        module_nodes.append(
            ModuleNodeResponse(
                module_id=module_id,
                file_count=file_count,
                symbol_count=symbol_count,
                avg_pagerank=avg_pagerank,
                doc_coverage_pct=doc_coverage_pct,
                hotspot_count=hotspot_count,
                dead_count=dead_count,
                has_decision=has_decision,
                primary_owner=primary_owner,
            )
        )

    # Build inter-module edges from file-level edges
    edge_result = await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    edges = edge_result.scalars().all()

    edge_counts: dict[tuple[str, str], int] = {}
    for e in edges:
        src_module = node_to_module.get(e.source_node_id)
        tgt_module = node_to_module.get(e.target_node_id)
        if src_module and tgt_module and src_module != tgt_module:
            key = (src_module, tgt_module)
            edge_counts[key] = edge_counts.get(key, 0) + 1

    module_edges = [
        ModuleEdgeResponse(source=src, target=tgt, edge_count=count)
        for (src, tgt), count in edge_counts.items()
    ]

    return ModuleGraphResponse(nodes=module_nodes, edges=module_edges)
