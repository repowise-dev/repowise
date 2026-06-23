"""Golden tests for the C4 builder.

Each test seeds an in-memory DB with a tiny but realistic fixture
(repository + graph_nodes + graph_edges + external_systems) and then
asserts the shape of build_l1 / build_l2 / build_l3 output.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.persistence import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    bulk_upsert_external_systems,
    link_graph_nodes_to_external_systems,
    upsert_repository,
)
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitMetadata,
    KnowledgeGraphProjectMeta,
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


# ---------------------------------------------------------------------------
# Phase 3 shaping: root naming, external/sibling exclusion, component depth,
# signal counts, derived L1 actors.
# ---------------------------------------------------------------------------


async def _seed_single_package(session):
    """A root-manifest repo (one pyproject.toml at the root) with a ``src``
    layout, a stray unresolved external node, and curated entry points.
    """
    repo = await upsert_repository(session, name="acme-tool", local_path="/tmp/acme")
    files = [
        "pyproject.toml",
        "README.md",
        "src/acme/cli/main.py",
        "src/acme/server/app.py",
        "src/acme/core/engine.py",
        "scripts/run.py",
    ]
    nodes = [
        {"node_id": f, "node_type": "file", "language": "python", "symbol_count": 2}
        for f in files
    ]
    # An unresolved-import target — must never become a file/component.
    nodes.append({"node_id": "external:@/lib/thing", "node_type": "file", "language": "python", "symbol_count": 0})
    await batch_upsert_graph_nodes(session, repo.id, nodes)

    session.add(
        KnowledgeGraphProjectMeta(
            repository_id=repo.id,
            entry_points_json=json.dumps(
                ["src/acme/cli/main.py", "src/acme/server/app.py", "scripts/run.py"]
            ),
        )
    )
    # One hotspot + one dead file so the counts have something to report.
    session.add(GitMetadata(repository_id=repo.id, file_path="src/acme/core/engine.py", is_hotspot=True))
    session.add(
        DeadCodeFinding(
            repository_id=repo.id,
            kind="unreachable_file",
            file_path="src/acme/cli/main.py",
            status="open",
            confidence=0.9,
            reason="",
        )
    )
    await session.commit()
    return repo


@pytest.mark.asyncio
async def test_root_container_uses_repo_name_not_dot(async_session):
    repo = await _seed_single_package(async_session)
    view = await c4_builder.build_l2(async_session, repo.id)
    root = next(c for c in view.containers if c.path == "")
    assert root.name == "acme-tool"
    assert root.name != "."


@pytest.mark.asyncio
async def test_external_nodes_excluded_from_counts_and_components(async_session):
    repo = await _seed_single_package(async_session)
    view = await c4_builder.build_l2(async_session, repo.id)
    root = next(c for c in view.containers if c.path == "")
    # 6 real files, not 7 — the external:@ node is excluded.
    assert root.file_count == 6

    l3 = await c4_builder.build_l3(async_session, repo.id, root.id)
    assert l3 is not None
    assert all("external:" not in c.id for c in l3.components)
    assert all("external:" not in c.path for c in l3.components)


@pytest.mark.asyncio
async def test_component_depth_skips_passthrough_and_names_root_bucket(async_session):
    repo = await _seed_single_package(async_session)
    root = next(
        c for c in (await c4_builder.build_l2(async_session, repo.id)).containers if c.path == ""
    )
    l3 = await c4_builder.build_l3(async_session, repo.id, root.id)
    assert l3 is not None
    names = {c.name for c in l3.components}
    # `src/acme/...` strips `src`, surfacing `acme` rather than a giant `src`;
    # the root-level files collapse into a labeled bucket, never "_root".
    assert "acme" in names
    assert "scripts" in names
    assert "_root" not in names
    assert "(root)" in names
    assert not any(c.id.endswith("/_root") or "_root" in c.id for c in l3.components)


@pytest.mark.asyncio
async def test_container_signal_counts_populated(async_session):
    repo = await _seed_single_package(async_session)
    view = await c4_builder.build_l2(async_session, repo.id)
    root = next(c for c in view.containers if c.path == "")
    assert root.hotspot_count == 1
    assert root.dead_count == 1


@pytest.mark.asyncio
async def test_l1_actors_derived_from_entry_points(async_session):
    repo = await _seed_single_package(async_session)
    view = await c4_builder.build_l1(async_session, repo.id)
    kinds = [p.kind for p in view.people]
    assert kinds == ["cli", "api", "developer"]
    # Every actor drives one edge into the system.
    actor_edges = [r for r in view.relations if r.target_id == view.system.id]
    assert len(actor_edges) == 3


@pytest.mark.asyncio
async def test_relation_labels_are_readable_with_coupling(async_session):
    repo = await _seed_monorepo(async_session)
    view = await c4_builder.build_l2(async_session, repo.id)
    cross = next(r for r in view.relations if r.source_id == "pkg:packages/web")
    assert cross.label == "imports"
    assert cross.coupling in {"loose", "moderate", "tight"}
    assert "+1" not in cross.label
