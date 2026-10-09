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


async def _seed_nodes_and_edges(session, local_path, nodes, edges=()):
    repo = await upsert_repository(session, name="shop", local_path=local_path)
    await batch_upsert_graph_nodes(
        session,
        repo.id,
        [{"node_id": n, "node_type": "file", "language": lang, "symbol_count": 1} for n, lang in nodes],
    )
    if edges:
        await batch_upsert_graph_edges(
            session,
            repo.id,
            [{"source_node_id": s, "target_node_id": t, "edge_type": e} for s, t, e in edges],
        )
    await session.commit()
    return repo


@pytest.mark.asyncio
async def test_dominant_language_ignores_markdown(async_session):
    nodes = [
        ("packages/docs-kit/package.json", "json"),
        ("packages/docs-kit/a.md", "markdown"),
        ("packages/docs-kit/b.md", "markdown"),
        ("packages/docs-kit/c.md", "markdown"),
        ("packages/docs-kit/index.ts", "typescript"),
    ]
    repo = await _seed_nodes_and_edges(async_session, "/nonexistent/shop", nodes)
    view = await c4_builder.build_l2(async_session, repo.id)
    (container,) = view.containers
    assert container.language == "typescript"
    assert container.file_count == 5


@pytest.mark.asyncio
async def test_fixture_manifest_not_container(async_session):
    nodes = [
        ("package.json", "json"),
        ("src/index.ts", "typescript"),
        ("tests/fixtures/app/package.json", "json"),
        ("tests/fixtures/app/index.ts", "typescript"),
        ("examples/demo/package.json", "json"),
        ("examples/demo/main.ts", "typescript"),
        ("packages/core/package.json", "json"),
        ("packages/core/src/lib.ts", "typescript"),
        ("packages/core/test/fixture/package.json", "json"),
    ]
    repo = await _seed_nodes_and_edges(async_session, "/nonexistent/shop", nodes)
    view = await c4_builder.build_l2(async_session, repo.id)
    by_path = {c.path: c for c in view.containers}
    assert set(by_path) == {"", "packages/core"}
    # Fixture and sample files fold into the nearest remaining container.
    assert by_path[""].file_count == 6
    assert by_path["packages/core"].file_count == 3
    # With no readable manifest a container is named by its relative path.
    assert by_path["packages/core"].name == "packages/core"
    assert by_path[""].name == "shop"


@pytest.mark.asyncio
async def test_fallback_top_level_dirs_skip_tests_and_examples(async_session):
    nodes = [
        ("app/main.go", "go"),
        ("tests/app_test.go", "go"),
        ("examples/hello/main.go", "go"),
    ]
    repo = await _seed_nodes_and_edges(async_session, "/nonexistent/shop", nodes)
    view = await c4_builder.build_l2(async_session, repo.id)
    assert [c.path for c in view.containers] == ["app"]


@pytest.mark.asyncio
async def test_container_named_from_manifest(async_session, tmp_path):
    (tmp_path / "packages" / "core").mkdir(parents=True)
    (tmp_path / "packages" / "core" / "package.json").write_text('{"name": "@shop/core"}')
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "shop-root"\n')
    nodes = [
        ("pyproject.toml", "toml"),
        ("shop/app.py", "python"),
        ("packages/core/package.json", "json"),
        ("packages/core/index.ts", "typescript"),
    ]
    repo = await _seed_nodes_and_edges(async_session, str(tmp_path), nodes)
    names = {c.path: c.name for c in (await c4_builder.build_l2(async_session, repo.id)).containers}
    assert names == {"": "shop-root", "packages/core": "@shop/core"}


async def _seed_two_containers_with_edges(session, edges):
    nodes = [
        ("packages/a/package.json", "json"),
        ("packages/a/index.ts", "typescript"),
        ("packages/a/tsconfig.json", "json"),
        ("packages/b/package.json", "json"),
        ("packages/b/index.ts", "typescript"),
    ]
    return await _seed_nodes_and_edges(session, "/nonexistent/shop", nodes, edges)


@pytest.mark.asyncio
async def test_l2_excludes_cochange(async_session):
    repo = await _seed_two_containers_with_edges(
        async_session, [("packages/a/index.ts", "packages/b/index.ts", "co_changes")]
    )
    assert (await c4_builder.build_l2(async_session, repo.id)).relations == []
    # The shared roll-up drops history everywhere, the export included.
    model = await c4_builder.build_model(async_session, repo.id)
    assert model.container_relations == [] and model.component_relations == []

    overlay = await c4_builder.build_l2(async_session, repo.id, include_co_changes=True)
    assert [(r.source_id, r.target_id, r.label) for r in overlay.relations] == [
        ("pkg:packages/a", "pkg:packages/b", "co-changes")
    ]


@pytest.mark.asyncio
async def test_l2_skips_edges_to_config_files(async_session):
    repo = await _seed_two_containers_with_edges(
        async_session,
        [
            ("packages/b/index.ts", "packages/a/tsconfig.json", "imports"),
            ("packages/b/index.ts", "packages/a/index.ts", "imports"),
        ],
    )
    (relation,) = (await c4_builder.build_l2(async_session, repo.id)).relations
    assert (relation.source_id, relation.target_id, relation.edge_count) == (
        "pkg:packages/b",
        "pkg:packages/a",
        1,
    )


async def _add_test_import(session, repo) -> None:
    """A test in web importing core: not a dependency of web on core."""
    await batch_upsert_graph_nodes(
        session,
        repo.id,
        [{"node_id": "packages/web/tests/test_api.py", "node_type": "file", "language": "python", "symbol_count": 1}],
    )
    await batch_upsert_graph_edges(
        session,
        repo.id,
        [{"source_node_id": "packages/web/tests/test_api.py", "target_node_id": "packages/core/ingestion/graph.py", "edge_type": "imports"}],
    )
    await session.commit()


def test_roll_up_drops_edges_touching_a_test_file():
    from repowise.core.analysis.c4.relations import roll_up_edges

    boxes = {"a/src/x.py": "a", "a/tests/test_x.py": "a", "b/src/y.py": "b"}
    rolled = roll_up_edges(
        [
            ("a/src/x.py", "b/src/y.py", "imports"),
            ("a/tests/test_x.py", "b/src/y.py", "imports"),
        ],
        boxes,
    )
    assert rolled == {("a", "b"): (1, frozenset({"imports"}))}


@pytest.mark.asyncio
async def test_container_view_and_dependencies_agree_without_tests(async_session):
    repo = await _seed_monorepo(async_session)
    await _add_test_import(async_session, repo)

    containers, relations = await c4_builder.container_dependencies(async_session, repo.id)
    view = await c4_builder.build_l2(async_session, repo.id)

    assert {c.path for c in containers} == {"packages/core", "packages/web"}
    internal = [(r.source_id, r.target_id, r.edge_count) for r in relations]
    assert internal == [("pkg:packages/web", "pkg:packages/core", 1)]
    in_view = [
        (r.source_id, r.target_id, r.edge_count)
        for r in view.relations
        if not r.target_id.startswith("ext:")
    ]
    assert in_view == internal


@pytest.mark.asyncio
async def test_container_dependencies_honour_exclusions(async_session):
    import pathspec

    repo = await _seed_monorepo(async_session)
    spec = pathspec.PathSpec.from_lines("gitwildmatch", ["packages/web/lib/"])
    _, relations = await c4_builder.container_dependencies(
        async_session, repo.id, exclude_spec=spec
    )
    assert relations == []


@pytest.mark.asyncio
async def test_overview_dependency_block_is_cached_and_empty_for_one_package(async_session):
    from types import SimpleNamespace

    from repowise.server.mcp_server.tool_overview.graph import (
        _build_package_dependencies,
        reset_cache,
    )

    reset_cache()
    repo = await _seed_monorepo(async_session)
    handle = SimpleNamespace(id=repo.id, head_commit="c1")
    expected = {
        "edges": [{"from": "packages/web", "to": "packages/core", "verb": "imports", "weight": 1}]
    }
    block = await _build_package_dependencies(async_session, handle)
    assert block == expected

    # Same index state: served from the cache, blind to a new edge. A new
    # commit re-derives it.
    await batch_upsert_graph_edges(
        async_session,
        repo.id,
        [{"source_node_id": "packages/web/app/page.tsx", "target_node_id": "packages/core/ingestion/graph.py", "edge_type": "imports"}],
    )
    await async_session.commit()
    block["edges"].clear()
    assert await _build_package_dependencies(async_session, handle) == expected
    moved = SimpleNamespace(id=repo.id, head_commit="c2")
    assert (await _build_package_dependencies(async_session, moved))["edges"][0]["weight"] == 2

    single = await upsert_repository(async_session, name="single", local_path="/tmp/single")
    await batch_upsert_graph_nodes(
        async_session,
        single.id,
        [
            {"node_id": "pyproject.toml", "node_type": "file", "language": "toml", "symbol_count": 0},
            {"node_id": "src/app.py", "node_type": "file", "language": "python", "symbol_count": 1},
            {"node_id": "src/util.py", "node_type": "file", "language": "python", "symbol_count": 1},
        ],
    )
    await batch_upsert_graph_edges(
        async_session,
        single.id,
        [{"source_node_id": "src/app.py", "target_node_id": "src/util.py", "edge_type": "imports"}],
    )
    await async_session.commit()
    single_handle = SimpleNamespace(id=single.id, head_commit="c1")
    assert await _build_package_dependencies(async_session, single_handle) == {}
