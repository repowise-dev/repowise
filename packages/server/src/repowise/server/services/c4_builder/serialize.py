"""Architecture-view serialization — builder dataclasses → response models.

Pure functions (no FastAPI imports): the HTTP router and any non-HTTP
consumer — e.g. an indexer precomputing per-snapshot artifacts — call the
same code so both serving paths emit identical shapes
(``model_dump(mode="json")`` for the artifact form).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.server.schemas import (
    ArchEdgeResponse,
    ArchitectureViewResponse,
    ArchLayerResponse,
    ArchNodeResponse,
    ArchSubGroupResponse,
    ArchTourStepResponse,
    C4ExternalSystemResponse,
)
from repowise.server.services.c4_builder.architecture import build_architecture_view
from repowise.server.services.c4_builder.models import (
    ArchEdge,
    ArchitectureView,
    ArchLayer,
    ArchNode,
    ArchTourStep,
    ExternalSystemView,
)


def external_system_response(e: ExternalSystemView) -> C4ExternalSystemResponse:
    return C4ExternalSystemResponse(
        id=e.id,
        name=e.name,
        display_name=e.display_name,
        category=e.category,
        ecosystem=e.ecosystem,
        version=e.version,
        io_kind=e.io_kind,
    )


def _arch_layer(layer: ArchLayer) -> ArchLayerResponse:
    return ArchLayerResponse(
        id=layer.id,
        name=layer.name,
        description=layer.description,
        node_ids=layer.node_ids,
        file_count=layer.file_count,
        complexity_distribution=layer.complexity_distribution,
        health_score=layer.health_score,
        sub_groups=[
            ArchSubGroupResponse(id=sg.id, name=sg.name, node_ids=sg.node_ids)
            for sg in layer.sub_groups
        ],
        display_order=layer.display_order,
    )


def _arch_node(n: ArchNode) -> ArchNodeResponse:
    return ArchNodeResponse(
        id=n.id,
        node_type=n.node_type,
        name=n.name,
        file_path=n.file_path,
        line_range=list(n.line_range) if n.line_range else None,
        summary=n.summary,
        complexity=n.complexity,
        tags=n.tags,
        language=n.language,
        pagerank=n.pagerank,
        pagerank_percentile=n.pagerank_percentile,
        betweenness=n.betweenness,
        in_degree=n.in_degree,
        out_degree=n.out_degree,
        community_id=n.community_id,
        is_entry_point=n.is_entry_point,
        is_test=n.is_test,
        is_hotspot=n.is_hotspot,
        is_dead=n.is_dead,
        has_doc=n.has_doc,
        primary_owner=n.primary_owner,
        primary_owner_pct=n.primary_owner_pct,
        bus_factor=n.bus_factor,
    )


def _arch_edge(e: ArchEdge) -> ArchEdgeResponse:
    return ArchEdgeResponse(
        source=e.source,
        target=e.target,
        edge_type=e.edge_type,
        direction=e.direction,
        weight=e.weight,
        confidence=e.confidence,
    )


def _arch_tour_step(s: ArchTourStep) -> ArchTourStepResponse:
    return ArchTourStepResponse(
        order=s.order,
        title=s.title,
        description=s.description,
        node_ids=s.node_ids,
        target_path=s.target_path,
        layer_id=s.layer_id,
        reason=s.reason,
        depth=s.depth,
        kind=s.kind,
        page_type=s.page_type,
    )


def architecture_view_response(view: ArchitectureView) -> ArchitectureViewResponse:
    """Serialize a built ArchitectureView into the API response model."""
    return ArchitectureViewResponse(
        project_name=view.project_name,
        project_description=view.project_description,
        layers=[_arch_layer(la) for la in view.layers],
        nodes=[_arch_node(n) for n in view.nodes],
        edges=[_arch_edge(e) for e in view.edges],
        tour=[_arch_tour_step(s) for s in view.tour],
        total_files=view.total_files,
        total_symbols=view.total_symbols,
        total_edges=view.total_edges,
        languages=view.languages,
        frameworks=view.frameworks,
        external_systems=[external_system_response(e) for e in view.external_systems],
        entry_points=view.entry_points,
        entry_candidates=view.entry_candidates,
    )


async def build_architecture_view_response(
    session: AsyncSession,
    repo_id: str,
    include_symbols: bool = False,
) -> ArchitectureViewResponse:
    """Build + serialize the architecture view in one call.

    The single entry point for both the HTTP endpoint and artifact
    precomputation.
    """
    view = await build_architecture_view(session, repo_id, include_symbols=include_symbols)
    return architecture_view_response(view)
