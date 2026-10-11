"""Direct unit tests for the architecture-view serialization layer.

tests/unit/server/test_architecture_api.py pins the wire shape through the
endpoint; these exercise the dataclass → response-model adapters without
FastAPI — the contract non-HTTP consumers (artifact precomputation) rely on.
"""

from __future__ import annotations

from repowise.server.services.c4_builder.models import (
    ArchEdge,
    ArchitectureView,
    ArchLayer,
    ArchNode,
    ArchSubGroup,
    ArchTourStep,
    ExternalSystemView,
)
from repowise.server.services.c4_builder.serialize import architecture_view_response


def _node(node_id: str = "file:src/main.py") -> ArchNode:
    return ArchNode(
        id=node_id,
        node_type="file",
        name="main.py",
        file_path="src/main.py",
        line_range=(1, 40),
        summary="entry point",
        complexity="low",
        tags=["entry"],
        language="python",
        pagerank=0.8,
        pagerank_percentile=0.99,
        betweenness=0.5,
        in_degree=0,
        out_degree=3,
        community_id=0,
        is_entry_point=True,
        is_test=False,
        is_hotspot=True,
        is_dead=False,
        has_doc=True,
        primary_owner="Alice",
        primary_owner_pct=0.7,
        bus_factor=2,
    )


def test_architecture_view_response_round_trips_all_fields():
    view = ArchitectureView(
        project_name="demo",
        project_description="a demo",
        layers=[
            ArchLayer(
                id="layer:ui",
                name="UI",
                description="front end",
                node_ids=["file:src/main.py"],
                file_count=1,
                complexity_distribution={"low": 1},
                health_score=8.5,
                sub_groups=[
                    ArchSubGroup(id="sg:forms", name="Forms", node_ids=["file:src/main.py"])
                ],
                display_order=1,
            )
        ],
        nodes=[_node()],
        edges=[
            ArchEdge(
                source="file:src/main.py",
                target="file:src/utils.py",
                edge_type="import",
                direction="out",
                weight=1.0,
                confidence=0.9,
            )
        ],
        tour=[
            ArchTourStep(
                order=1,
                title="Start here",
                description="the entry point",
                node_ids=["file:src/main.py"],
                target_path="src/main.py",
                layer_id="layer:ui",
                reason="entry",
                depth=0,
                kind="entry_point",
                page_type="module",
            )
        ],
        total_files=2,
        total_symbols=10,
        total_edges=1,
        languages=["python"],
        frameworks=["fastapi"],
        external_systems=[
            ExternalSystemView(
                id="ext:redis",
                name="redis",
                display_name="Redis",
                category="cache",
                ecosystem="pypi",
                version="5.0",
            )
        ],
        entry_points=["src/main.py"],
        entry_candidates=["src/cli.py"],
    )

    resp = architecture_view_response(view)

    assert resp.project_name == "demo"
    layer = resp.layers[0]
    assert (layer.id, layer.display_order, layer.health_score) == ("layer:ui", 1, 8.5)
    assert layer.sub_groups[0].name == "Forms"
    node = resp.nodes[0]
    assert node.line_range == [1, 40]  # tuple becomes a JSON-friendly list
    assert node.primary_owner == "Alice"
    assert node.is_hotspot is True
    edge = resp.edges[0]
    assert (edge.source, edge.edge_type, edge.confidence) == (
        "file:src/main.py",
        "import",
        0.9,
    )
    step = resp.tour[0]
    assert (step.target_path, step.layer_id, step.depth, step.kind, step.page_type) == (
        "src/main.py",
        "layer:ui",
        0,
        "entry_point",
        "module",
    )
    ext = resp.external_systems[0]
    assert (ext.display_name, ext.category) == ("Redis", "cache")
    assert resp.entry_points == ["src/main.py"]
    assert resp.entry_candidates == ["src/cli.py"]


def test_architecture_view_response_none_line_range():
    view = ArchitectureView(
        project_name="demo",
        project_description="",
        layers=[],
        nodes=[
            ArchNode(
                **{
                    **_node().__dict__,
                    "line_range": None,
                }
            )
        ],
        edges=[],
        tour=[],
        total_files=1,
        total_symbols=0,
        total_edges=0,
        languages=[],
        frameworks=[],
        external_systems=[],
    )

    assert architecture_view_response(view).nodes[0].line_range is None
