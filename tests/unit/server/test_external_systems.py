"""Tests for the /api/repos/{repo_id}/external-systems dependency registry."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select

from repowise.core.persistence.crud.external_systems import (
    build_external_system_link_map,
    link_graph_nodes_to_external_systems,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import ExternalSystem, GraphEdge, GraphNode
from tests.unit.server.conftest import create_test_repo


async def _seed(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        session.add_all(
            [
                ExternalSystem(
                    repository_id=repo_id,
                    name="react",
                    display_name="React",
                    ecosystem="npm",
                    category="framework",
                    version="^19.0.0",
                    declared_in="packages/web/package.json",
                    is_dev_dep=False,
                ),
                ExternalSystem(
                    repository_id=repo_id,
                    name="vitest",
                    display_name="Vitest",
                    ecosystem="npm",
                    category="tool",
                    version="^4.1.5",
                    declared_in="packages/ui/package.json",
                    is_dev_dep=True,
                ),
                ExternalSystem(
                    repository_id=repo_id,
                    name="fastapi",
                    display_name="FastAPI",
                    ecosystem="pypi",
                    category="framework",
                    version=">=0.110",
                    declared_in="packages/server/pyproject.toml",
                    is_dev_dep=False,
                ),
            ]
        )
        await session.flush()


async def _seed_summary(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        react_web = ExternalSystem(
            repository_id=repo_id,
            name="react",
            display_name="React",
            ecosystem="npm",
            category="framework",
            version="^19.0.0",
            declared_in="packages/web/package.json",
            is_dev_dep=False,
        )
        react_ui = ExternalSystem(
            repository_id=repo_id,
            name="react",
            display_name="React",
            ecosystem="npm",
            category="framework",
            version="^18.0.0",
            declared_in="packages/ui/package.json",
            is_dev_dep=True,
        )
        next_package = ExternalSystem(
            repository_id=repo_id,
            name="next",
            display_name="Next",
            ecosystem="npm",
            category="framework",
            version="~15.5.0",
            declared_in="packages/web/package.json",
            is_dev_dep=False,
        )
        vitest = ExternalSystem(
            repository_id=repo_id,
            name="vitest",
            display_name="Vitest",
            ecosystem="npm",
            category="tool",
            version="^4.1.0",
            declared_in="packages/ui/package.json",
            is_dev_dep=True,
        )
        radix = ExternalSystem(
            repository_id=repo_id,
            name="@radix-ui/react-dialog",
            display_name="Radix Dialog",
            ecosystem="npm",
            category="library",
            version="^1.0.0",
            declared_in="packages/web/package.json",
            is_dev_dep=False,
        )
        serde = ExternalSystem(
            repository_id=repo_id,
            name="serde",
            display_name="Serde",
            ecosystem="cargo",
            category="library",
            version="1",
            declared_in="crates/core/Cargo.toml",
            is_dev_dep=False,
        )
        auxiliary = ExternalSystem(
            repository_id=repo_id,
            name="aux-only",
            display_name="Aux only",
            ecosystem="npm",
            category="library",
            version="1.0.0",
            declared_in=".claude/worktrees/demo/package.json",
            is_dev_dep=False,
        )
        session.add_all([react_web, react_ui, next_package, vitest, radix, serde, auxiliary])
        await session.flush()

        session.add_all(
            [
                GraphNode(
                    repository_id=repo_id,
                    node_id="src/a.ts",
                    language="typescript",
                    community_id=1,
                    community_meta_json='{"label":"Web runtime"}',
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="src/b.ts",
                    language="typescript",
                    community_id=2,
                    community_meta_json='{"label":"Shared UI"}',
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="src/dialog.ts",
                    language="typescript",
                    community_id=1,
                    community_meta_json='{"label":"Web runtime"}',
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="src/lib.rs",
                    language="rust",
                    community_id=3,
                    community_meta_json='{"label":"Core"}',
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="src/page.ts",
                    language="typescript",
                    community_id=1,
                    community_meta_json='{"label":"Web runtime"}',
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id=".claude/worktrees/demo/src/a.ts",
                    language="typescript",
                    community_id=4,
                    community_meta_json='{"label":"Auxiliary"}',
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="external:react",
                    language="external",
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="external:react/jsx-runtime",
                    language="external",
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="external:next/navigation",
                    language="external",
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="external:@radix-ui/react-dialog/subpath",
                    language="external",
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="external:serde::Deserialize",
                    language="external",
                ),
            ]
        )
        session.add_all(
            [
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id="src/a.ts",
                    target_node_id="external:react",
                    edge_type="imports",
                ),
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id="src/dialog.ts",
                    target_node_id="external:@radix-ui/react-dialog/subpath",
                    edge_type="imports",
                ),
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id="src/lib.rs",
                    target_node_id="external:serde::Deserialize",
                    edge_type="imports",
                ),
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id="src/a.ts",
                    target_node_id="external:react/jsx-runtime",
                    edge_type="imports",
                ),
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id="src/b.ts",
                    target_node_id="external:react",
                    edge_type="imports",
                ),
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id=".claude/worktrees/demo/src/a.ts",
                    target_node_id="external:react",
                    edge_type="imports",
                ),
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id="src/page.ts",
                    target_node_id="external:next/navigation",
                    edge_type="imports",
                ),
                # Non-import edges do not constitute declared-package usage.
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id="src/call.ts",
                    target_node_id="external:react",
                    edge_type="calls",
                ),
            ]
        )
        await session.flush()
        linked = await link_graph_nodes_to_external_systems(
            session,
            repo_id,
            {
                "react": react_web.id,
                "next": next_package.id,
                "vitest": vitest.id,
                "@radix-ui/react-dialog": radix.id,
                "serde": serde.id,
            },
        )
        assert linked == 5


@pytest.mark.asyncio
async def test_registry_lists_all_rows(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/external-systems")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["prod_count"] == 2
    assert data["dev_count"] == 1
    assert data["ecosystems"] == ["npm", "pypi"]
    assert data["manifests"] == [
        "packages/server/pyproject.toml",
        "packages/ui/package.json",
        "packages/web/package.json",
    ]
    # Sorted by category prominence (framework first), then name.
    assert [e["name"] for e in data["items"]] == ["fastapi", "react", "vitest"]
    react = data["items"][1]
    assert react["display_name"] == "React"
    assert react["version"] == "^19.0.0"
    assert react["declared_in"] == "packages/web/package.json"
    assert react["is_dev_dep"] is False


@pytest.mark.asyncio
async def test_registry_empty(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/external-systems")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_linker_preserves_cross_ecosystem_ambiguity_and_prefixed_names(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    systems = [
        {"name": "shared", "ecosystem": "npm", "declared_in": "package.json"},
        {"name": "shared", "ecosystem": "pypi", "declared_in": "pyproject.toml"},
        {
            "name": "Newtonsoft.Json",
            "ecosystem": "nuget",
            "declared_in": "app.csproj",
        },
        {"name": "http", "ecosystem": "pub", "declared_in": "pubspec.yaml"},
    ]
    async with get_session(app.state.session_factory) as session:
        rows = [
            ExternalSystem(
                repository_id=repo["id"],
                display_name=system["name"],
                category="library",
                **system,
            )
            for system in systems
        ]
        session.add_all(rows)
        await session.flush()
        id_map = {(row.name, row.declared_in): row.id for row in rows}
        links = build_external_system_link_map(systems, id_map)
        assert links["shared"] is None
        assert links["npm:shared"] == rows[0].id
        node_ids = [
            "external:shared",
            "external:npm:shared",
            "external:nuget:Newtonsoft.Json",
            "external:pub:http",
        ]
        session.add_all(
            GraphNode(
                repository_id=repo["id"],
                node_id=node_id,
                language="external",
                external_system_id=(rows[0].id if node_id == "external:shared" else None),
            )
            for node_id in node_ids
        )
        await session.flush()

        assert await link_graph_nodes_to_external_systems(session, repo["id"], links) == 4
        linked = {
            node.node_id: node.external_system_id
            for node in (
                await session.execute(
                    select(GraphNode).where(GraphNode.repository_id == repo["id"])
                )
            ).scalars()
        }
        assert linked["external:shared"] is None
        assert linked["external:npm:shared"] == rows[0].id
        assert linked["external:nuget:Newtonsoft.Json"] == rows[2].id
        assert linked["external:pub:http"] == rows[3].id


@pytest.mark.asyncio
async def test_summary_joins_unique_packages_to_import_evidence(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed_summary(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/external-systems/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_packages"] == 5
    assert data["total_declarations"] == 6
    assert data["returned"] == 5
    assert data["truncated"] is False
    assert data["scope"] == "primary"
    assert data["excluded_declarations"] == 1
    assert data["linked_packages"] == 4
    assert data["unlinked_packages"] == 1
    assert data["observed_packages"] == 4
    assert data["ecosystems"] == ["cargo", "npm"]

    by_name = {item["name"]: item for item in data["items"]}
    react = by_name["react"]
    assert react["versions"] == ["^18.0.0", "^19.0.0"]
    assert react["versions_total"] == 2
    assert react["versions_truncated"] is False
    assert react["multiple_versions"] is True
    assert react["import_edge_count"] == 3
    assert react["importing_file_count"] == 2
    assert react["link_state"] == "linked"
    assert by_name["@radix-ui/react-dialog"]["import_edge_count"] == 1
    assert by_name["serde"]["import_edge_count"] == 1
    assert by_name["vitest"]["link_state"] == "unlinked"
    assert "aux-only" not in by_name

    all_data = (
        await client.get(f"/api/repos/{repo['id']}/external-systems/summary?scope=all")
    ).json()
    all_react = next(item for item in all_data["items"] if item["name"] == "react")
    assert all_react["import_edge_count"] == 4
    assert all_react["importing_file_count"] == 3


@pytest.mark.asyncio
async def test_summary_query_count_is_constant(client: AsyncClient, app, test_engine) -> None:
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        session.add_all(
            [
                ExternalSystem(
                    repository_id=repo["id"],
                    name=f"package-{index}",
                    display_name=f"Package {index}",
                    ecosystem="npm",
                    category="library",
                    version="1.0.0",
                    declared_in=f"packages/p{index}/package.json",
                    is_dev_dep=False,
                )
                for index in range(250)
            ]
        )
        await session.flush()

    statements: list[str] = []

    def record(*args) -> None:
        statements.append(str(args[2]))

    event.listen(test_engine.sync_engine, "before_cursor_execute", record)
    try:
        resp = await client.get(f"/api/repos/{repo['id']}/external-systems/summary")
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", record)

    assert resp.status_code == 200
    assert resp.json()["total_packages"] == 250
    assert resp.json()["returned"] == 200
    assert resp.json()["truncated"] is True
    selects = [statement for statement in statements if statement.lstrip().startswith("SELECT")]
    assert len(selects) == 3


@pytest.mark.asyncio
async def test_summary_bounds_pages_versions_and_can_include_auxiliary_scope(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        session.add_all(
            [
                ExternalSystem(
                    repository_id=repo["id"],
                    name="many-versions",
                    display_name="Many versions",
                    ecosystem="npm",
                    category="library",
                    version=f"{version}.0.0",
                    declared_in=f"packages/p{version}/package.json",
                    is_dev_dep=False,
                )
                for version in range(8)
            ]
            + [
                ExternalSystem(
                    repository_id=repo["id"],
                    name="aux-only",
                    display_name="Aux only",
                    ecosystem="npm",
                    category="library",
                    version="1.0.0",
                    declared_in="local-stash/example/package.json",
                    is_dev_dep=True,
                )
            ]
        )
        await session.flush()

    primary = (await client.get(f"/api/repos/{repo['id']}/external-systems/summary?limit=1")).json()
    assert primary["returned"] == 1
    assert primary["total_packages"] == 1
    assert primary["excluded_declarations"] == 1
    item = primary["items"][0]
    assert item["versions_total"] == 8
    assert len(item["versions"]) == 5
    assert item["versions_truncated"] is True

    all_scopes = (
        await client.get(
            f"/api/repos/{repo['id']}/external-systems/summary?scope=all&limit=1&offset=1"
        )
    ).json()
    assert all_scopes["scope"] == "all"
    assert all_scopes["total_packages"] == 2
    assert all_scopes["returned"] == 1
    assert all_scopes["offset"] == 1
    assert all_scopes["excluded_declarations"] == 0

    too_large = await client.get(f"/api/repos/{repo['id']}/external-systems/summary?limit=401")
    assert too_large.status_code == 422


@pytest.mark.asyncio
async def test_summary_reports_auxiliary_only_declarations(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        session.add(
            ExternalSystem(
                repository_id=repo["id"],
                name="aux-only",
                display_name="Aux only",
                ecosystem="npm",
                category="library",
                version="1.0.0",
                declared_in="local-stash/example/package.json",
                is_dev_dep=True,
            )
        )
        await session.flush()

    data = (await client.get(f"/api/repos/{repo['id']}/external-systems/summary")).json()
    assert data["items"] == []
    assert data["total_packages"] == 0
    assert data["excluded_declarations"] == 1


@pytest.mark.asyncio
async def test_summary_empty(client: AsyncClient) -> None:
    repo = await create_test_repo(client)

    resp = await client.get(f"/api/repos/{repo['id']}/external-systems/summary")

    assert resp.status_code == 200
    assert resp.json() == {
        "items": [],
        "returned": 0,
        "total_packages": 0,
        "limit": 200,
        "offset": 0,
        "truncated": False,
        "scope": "primary",
        "excluded_declarations": 0,
        "total_declarations": 0,
        "runtime_packages": 0,
        "dev_only_packages": 0,
        "observed_packages": 0,
        "linked_packages": 0,
        "unlinked_packages": 0,
        "linked_without_imports": 0,
        "ecosystems": [],
        "manifest_count": 0,
    }


@pytest.mark.asyncio
async def test_relationship_graph_is_aggregate_first_bounded_and_honest(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed_summary(app.state.session_factory, repo["id"])

    response = await client.get(
        f"/api/repos/{repo['id']}/external-systems/npm:react/graph?node_limit=1&edge_limit=1"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["package_key"] == "npm:react"
    assert data["package_node_id"] == "package:npm:react"
    assert data["match_basis"] == "mixed"
    assert data["matched_external_nodes_total"] == 2
    assert data["evidence_target_limit"] == 200
    assert data["evidence_truncated"] is False
    assert {target["match_basis"] for target in data["matched_external_nodes"]} == {
        "exact",
        "subpath",
    }
    assert data["aggregate_total"] == 2
    assert data["aggregate_returned"] == 1
    assert data["edge_total"] == 2
    assert data["edge_returned"] == 1
    assert data["importing_file_total"] == 2
    assert data["import_edge_total"] == 3
    assert data["truncated"] is True
    assert data["nodes"][0] == {
        "aggregate_key": "community:1",
        "label": "Web runtime",
        "community_id": 1,
        "importing_file_count": 1,
        "import_edge_count": 2,
        "top_file": "src/a.ts",
    }
    assert data["edges"] == [
        {
            "source": "community:1",
            "target": "package:npm:react",
            "import_edge_count": 2,
        }
    ]


@pytest.mark.asyncio
async def test_relationship_graph_handles_unresolved_missing_and_scoped_packages(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed_summary(app.state.session_factory, repo["id"])

    unresolved = (
        await client.get(f"/api/repos/{repo['id']}/external-systems/npm:vitest/graph")
    ).json()
    assert unresolved["match_basis"] == "unresolved"
    assert unresolved["matched_external_nodes"] == []
    assert unresolved["nodes"] == []
    assert unresolved["importing_file_total"] == 0
    assert unresolved["truncated"] is False

    scoped = await client.get(
        f"/api/repos/{repo['id']}/external-systems/npm:%40radix-ui%2Freact-dialog/graph"
    )
    assert scoped.status_code == 200
    assert scoped.json()["package_key"] == "npm:@radix-ui/react-dialog"
    assert scoped.json()["match_basis"] == "subpath"

    missing = await client.get(f"/api/repos/{repo['id']}/external-systems/npm:not-declared/graph")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_relationship_file_expansion_is_independently_bounded_and_paginated(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed_summary(app.state.session_factory, repo["id"])

    first = (
        await client.get(
            f"/api/repos/{repo['id']}/external-systems/npm:react/graph/files",
            params={"aggregate_key": "community:1", "limit": 1},
        )
    ).json()
    assert first["total"] == 1
    assert first["returned"] == 1
    assert first["items"][0] == {
        "path": "src/a.ts",
        "language": "typescript",
        "import_edge_count": 2,
        "matched_external_node_count": 2,
    }
    assert first["truncated"] is False

    second = (
        await client.get(
            f"/api/repos/{repo['id']}/external-systems/npm:react/graph/files",
            params={"aggregate_key": "community:2", "limit": 1, "offset": 1},
        )
    ).json()
    assert second["total"] == 1
    assert second["items"] == []
    assert second["truncated"] is False

    invalid = await client.get(
        f"/api/repos/{repo['id']}/external-systems/npm:react/graph/files",
        params={"aggregate_key": "directory:src"},
    )
    assert invalid.status_code == 422
    too_large = await client.get(
        f"/api/repos/{repo['id']}/external-systems/npm:react/graph/files",
        params={"aggregate_key": "community:1", "limit": 101},
    )
    assert too_large.status_code == 422

    missing = await client.get(
        f"/api/repos/{repo['id']}/external-systems/npm:not-declared/graph/files",
        params={"aggregate_key": "community:1"},
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_relationship_graph_and_expansion_share_the_same_capped_target_evidence(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        package = ExternalSystem(
            repository_id=repo["id"],
            name="many-targets",
            display_name="Many targets",
            ecosystem="npm",
            category="library",
            declared_in="package.json",
            is_dev_dep=False,
        )
        session.add(package)
        await session.flush()
        session.add_all(
            [
                GraphNode(
                    repository_id=repo["id"],
                    node_id=f"external:many-targets/{index:03}",
                    node_type="external",
                    language="external",
                    external_system_id=package.id,
                )
                for index in range(201)
            ]
            + [
                GraphNode(
                    repository_id=repo["id"],
                    node_id=f"src/file-{index:03}.ts",
                    language="typescript",
                    community_id=9,
                    community_meta_json='{"label":"Many consumers"}',
                )
                for index in range(201)
            ]
        )
        session.add_all(
            GraphEdge(
                repository_id=repo["id"],
                source_node_id=f"src/file-{index:03}.ts",
                target_node_id=f"external:many-targets/{index:03}",
                edge_type="imports",
            )
            for index in range(201)
        )
        await session.flush()

    graph = (
        await client.get(f"/api/repos/{repo['id']}/external-systems/npm:many-targets/graph")
    ).json()
    assert graph["matched_external_nodes_total"] == 201
    assert graph["evidence_target_limit"] == 200
    assert graph["evidence_truncated"] is True
    assert graph["importing_file_total"] == 200

    files = (
        await client.get(
            f"/api/repos/{repo['id']}/external-systems/npm:many-targets/graph/files",
            params={"aggregate_key": "community:9", "limit": 100},
        )
    ).json()
    assert files["total"] == 200
    assert files["returned"] == 100
    assert files["truncated"] is True


@pytest.mark.asyncio
async def test_relationship_graph_query_count_is_constant(
    client: AsyncClient, app, test_engine
) -> None:
    repo = await create_test_repo(client)
    await _seed_summary(app.state.session_factory, repo["id"])
    statements: list[str] = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", record)
    try:
        response = await client.get(f"/api/repos/{repo['id']}/external-systems/npm:react/graph")
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", record)

    assert response.status_code == 200
    selects = [statement for statement in statements if statement.lstrip().startswith("SELECT")]
    # Declarations, resolved external nodes, then the edges reaching them. The
    # count is what matters: it does not grow with importers, communities or
    # declared packages. Composition moved to a session-free fold, so the third
    # read replaces the aggregate the graph query used to push down.
    assert len(selects) == 3
