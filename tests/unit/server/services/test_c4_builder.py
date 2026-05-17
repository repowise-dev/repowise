"""Golden tests for the C4 builder.

Each test seeds an in-memory DB with a tiny but realistic fixture
(repository + graph_nodes + graph_edges + external_systems) and then
asserts the shape of build_l1 / build_l2 / build_l3 output.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    bulk_upsert_external_systems,
    link_graph_nodes_to_external_systems,
    upsert_repository,
)
from repowise.server.services import c4_builder


async def _seed_monorepo(session):
    """Two containers — packages/core and packages/web — with one cross-edge
    and a single external dep on each side.
    """
    repo = await upsert_repository(session, name="demo", local_path="/tmp/demo")

    files = [
        "packages/core/ingestion/parser.py",
        "packages/core/ingestion/graph.py",
        "packages/core/persistence/models.py",
        "packages/web/app/page.tsx",
        "packages/web/lib/api.ts",
    ]
    nodes = [
        {"node_id": f, "node_type": "file", "language": "python" if f.endswith(".py") else "typescript", "symbol_count": 1}
        for f in files
    ]
    # External nodes for imports
    nodes.append({"node_id": "external:fastapi", "node_type": "file", "language": "python", "symbol_count": 0})
    nodes.append({"node_id": "external:react", "node_type": "file", "language": "typescript", "symbol_count": 0})
    await batch_upsert_graph_nodes(session, repo.id, nodes)

    edges = [
        # Within container
        {"source_node_id": "packages/core/ingestion/parser.py", "target_node_id": "packages/core/ingestion/graph.py", "edge_type": "imports"},
        # Cross container
        {"source_node_id": "packages/web/lib/api.ts", "target_node_id": "packages/core/persistence/models.py", "edge_type": "imports"},
        # To externals
        {"source_node_id": "packages/core/ingestion/parser.py", "target_node_id": "external:fastapi", "edge_type": "imports"},
        {"source_node_id": "packages/web/app/page.tsx", "target_node_id": "external:react", "edge_type": "imports"},
    ]
    await batch_upsert_graph_edges(session, repo.id, edges)

    externals = [
        {"name": "fastapi", "display_name": "FastAPI", "ecosystem": "pypi", "category": "framework", "version": "0.110", "declared_in": "packages/core/pyproject.toml", "is_dev_dep": False},
        {"name": "react", "display_name": "React", "ecosystem": "npm", "category": "framework", "version": "^18", "declared_in": "packages/web/package.json", "is_dev_dep": False},
    ]
    id_map = await bulk_upsert_external_systems(session, repo.id, externals)
    name_to_id = {name: sid for (name, _), sid in id_map.items()}
    await link_graph_nodes_to_external_systems(session, repo.id, name_to_id)
    await session.commit()
    return repo


@pytest.mark.asyncio
async def test_build_l1_lists_externals_and_user(async_session):
    repo = await _seed_monorepo(async_session)
    view = await c4_builder.build_l1(async_session, repo.id)

    assert view.system.name == "demo"
    assert [p.id for p in view.people] == ["person:user"]
    assert {e.name for e in view.external_systems} == {"fastapi", "react"}
    # Every external has an edge from system; plus one user→system edge
    assert any(r.source_id == "person:user" and r.target_id == view.system.id for r in view.relations)
    assert sum(1 for r in view.relations if r.source_id == view.system.id) == 2


@pytest.mark.asyncio
async def test_build_l2_detects_containers_and_aggregates_edges(async_session):
    repo = await _seed_monorepo(async_session)
    view = await c4_builder.build_l2(async_session, repo.id)

    container_paths = {c.path for c in view.containers}
    assert container_paths == {"packages/core", "packages/web"}

    by_path = {c.path: c for c in view.containers}
    assert by_path["packages/core"].language == "python"
    assert by_path["packages/web"].language == "typescript"
    assert by_path["packages/core"].file_count == 3
    assert by_path["packages/web"].file_count == 2

    # web → core (one file-level edge rolled up)
    relations = {(r.source_id, r.target_id): r for r in view.relations}
    assert ("pkg:packages/web", "pkg:packages/core") in relations
    cross = relations[("pkg:packages/web", "pkg:packages/core")]
    assert cross.edge_count == 1

    # External edges
    assert ("pkg:packages/core", "ext:fastapi") in relations
    assert ("pkg:packages/web", "ext:react") in relations

    # No self-loops
    assert all(r.source_id != r.target_id for r in view.relations)


@pytest.mark.asyncio
async def test_build_l3_returns_components_and_filters_externals(async_session):
    repo = await _seed_monorepo(async_session)
    view = await c4_builder.build_l3(async_session, repo.id, "pkg:packages/core")
    assert view is not None
    assert view.container.path == "packages/core"
    comp_names = {c.name for c in view.components}
    assert comp_names == {"ingestion", "persistence"}

    # Only fastapi should appear under L3 of packages/core (not react)
    assert {e.name for e in view.external_systems} == {"fastapi"}


@pytest.mark.asyncio
async def test_build_l3_unknown_container_returns_none(async_session):
    repo = await _seed_monorepo(async_session)
    view = await c4_builder.build_l3(async_session, repo.id, "pkg:does/not/exist")
    assert view is None
