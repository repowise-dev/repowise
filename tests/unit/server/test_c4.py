"""HTTP-level tests for /api/graph/{repo_id}/c4/{l1,l2,l3}."""

from __future__ import annotations

import tempfile
from pathlib import Path

from httpx import AsyncClient

from repowise.core.ids import ComponentId, parse
from repowise.core.persistence import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    bulk_upsert_external_systems,
    link_graph_nodes_to_external_systems,
)


async def create_test_repo(client: AsyncClient) -> dict:
    """Local copy of tests.unit.server.conftest.create_test_repo so this
    module collects standalone (conftest helpers are not importable as a
    module from every pytest entry point)."""
    repo_dir = Path(tempfile.mkdtemp()) / "test-repo"
    repo_dir.mkdir(exist_ok=True)
    (repo_dir / ".git").mkdir(exist_ok=True)
    resp = await client.post(
        "/api/repos",
        json={
            "index": False,
            "name": "test-repo",
            "local_path": str(repo_dir),
            "url": "https://github.com/example/test-repo",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _seed_two_containers(client: AsyncClient, app) -> str:
    """Create a repo + a tiny graph + 2 external deps. Returns repo_id."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]

    async with app.state.session_factory() as session:
        nodes = [
            {"node_id": "packages/core/parser.py", "node_type": "file", "language": "python", "symbol_count": 2},
            {"node_id": "packages/core/graph.py", "node_type": "file", "language": "python", "symbol_count": 3},
            {"node_id": "packages/web/page.tsx", "node_type": "file", "language": "typescript", "symbol_count": 1},
            {"node_id": "external:fastapi", "node_type": "file", "language": "python", "symbol_count": 0},
            {"node_id": "external:react", "node_type": "file", "language": "typescript", "symbol_count": 0},
        ]
        await batch_upsert_graph_nodes(session, repo_id, nodes)
        await batch_upsert_graph_edges(session, repo_id, [
            {"source_node_id": "packages/web/page.tsx", "target_node_id": "packages/core/graph.py", "edge_type": "imports"},
            {"source_node_id": "packages/core/parser.py", "target_node_id": "external:fastapi", "edge_type": "imports"},
            {"source_node_id": "packages/web/page.tsx", "target_node_id": "external:react", "edge_type": "imports"},
        ])
        id_map = await bulk_upsert_external_systems(session, repo_id, [
            {"name": "fastapi", "display_name": "FastAPI", "ecosystem": "pypi", "category": "framework", "version": "0.110", "declared_in": "packages/core/pyproject.toml", "is_dev_dep": False},
            {"name": "react", "display_name": "React", "ecosystem": "npm", "category": "framework", "version": "^18", "declared_in": "packages/web/package.json", "is_dev_dep": False},
        ])
        name_to_id = {n: sid for (n, _), sid in id_map.items()}
        await link_graph_nodes_to_external_systems(session, repo_id, name_to_id)
        await session.commit()
    return repo_id


async def test_l1_endpoint_returns_system_and_externals(client: AsyncClient, app) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(f"/api/graph/{repo_id}/c4/l1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["system"]["name"] == "test-repo"
    assert [p["id"] for p in body["people"]] == ["person:user"]
    assert {e["name"] for e in body["external_systems"]} == {"fastapi", "react"}
    # User → system + system → 2 externals
    assert len(body["relations"]) == 3


async def test_l2_endpoint_returns_containers_and_aggregated_edges(client: AsyncClient, app) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(f"/api/graph/{repo_id}/c4/l2")
    assert resp.status_code == 200
    body = resp.json()
    paths = {c["path"] for c in body["containers"]}
    assert paths == {"packages/core", "packages/web"}

    edges = {(r["source_id"], r["target_id"]) for r in body["relations"]}
    assert ("pkg:packages/web", "pkg:packages/core") in edges
    assert ("pkg:packages/core", "ext:fastapi") in edges
    assert ("pkg:packages/web", "ext:react") in edges


async def test_l3_endpoint_requires_container_id(client: AsyncClient, app) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(f"/api/graph/{repo_id}/c4/l3")
    assert resp.status_code == 422  # missing query param


async def test_l3_endpoint_returns_components_for_container(client: AsyncClient, app) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(
        f"/api/graph/{repo_id}/c4/l3",
        params={"container_id": "pkg:packages/core"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["container"]["path"] == "packages/core"
    # All files in packages/core sit at the container root → the labeled root
    # bucket, never the leaky "_root" token.
    comp_names = {c["name"] for c in body["components"]}
    assert comp_names == {"(root)"}
    comp_ids = {c["id"] for c in body["components"]}
    assert "_root" not in comp_ids
    # The root bucket must not smuggle the symbol separator into a ``cmp:`` id:
    # ``cmp:packages/core::root`` parses back as a symbol called ``root``.
    assert all("::" not in cid for cid in comp_ids)
    assert all(parse(cid) == ComponentId("packages/core", is_root_bucket=True) for cid in comp_ids)
    # Only fastapi (used from packages/core), react is filtered out
    assert {e["name"] for e in body["external_systems"]} == {"fastapi"}


async def test_l3_endpoint_returns_404_for_unknown_container(client: AsyncClient, app) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(
        f"/api/graph/{repo_id}/c4/l3",
        params={"container_id": "pkg:does/not/exist"},
    )
    assert resp.status_code == 404


async def test_l1_endpoint_on_empty_repo(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/graph/{repo['id']}/c4/l1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["external_systems"] == []
    assert body["system"]["name"] == "test-repo"


async def test_structurizr_endpoint_returns_a_model_fragment(
    client: AsyncClient, app
) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(f"/api/graph/{repo_id}/c4/structurizr")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert 'filename="repowise-model.dsl"' in resp.headers["content-disposition"]
    body = resp.text
    assert body.startswith("#")
    assert "\nmodel {\n" in body
    assert "container " in body


async def test_structurizr_endpoint_standalone_returns_a_workspace(
    client: AsyncClient, app
) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(
        f"/api/graph/{repo_id}/c4/structurizr",
        params={"standalone": "true", "components": "true"},
    )
    assert resp.status_code == 200
    assert 'filename="workspace.dsl"' in resp.headers["content-disposition"]
    assert "workspace " in resp.text
    assert "views {" in resp.text
    assert "component " in resp.text


async def test_structurizr_endpoint_can_drop_externals(client: AsyncClient, app) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(
        f"/api/graph/{repo_id}/c4/structurizr", params={"externals": "false"}
    )
    assert resp.status_code == 200
    assert "FastAPI" not in resp.text


async def test_structurizr_endpoint_on_an_empty_repo(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/graph/{repo['id']}/c4/structurizr")
    assert resp.status_code == 200
    assert "softwareSystem" in resp.text


def test_an_awkward_directory_name_still_yields_a_usable_id() -> None:
    """A directory named ``a::b`` is legal on POSIX and reaches these helpers.

    The path is repo-derived and lands on the request path with no handler
    above it, so a raise here would be a 500 on the C4 endpoints and a hard
    abort of the export. Prefixed ids are unambiguous whatever they contain,
    so these round-trip instead.
    """
    from repowise.core.ids import ComponentId, ContainerId, parse
    from repowise.server.services.c4_builder.components import component_id
    from repowise.server.services.c4_builder.containers import container_id

    assert parse(container_id("weird::pkg")) == ContainerId("weird::pkg")
    assert parse(component_id("weird::dir")) == ComponentId("weird::dir")
    assert parse(component_id("weird::dir", is_root_bucket=True)) == ComponentId(
        "weird::dir", is_root_bucket=True
    )


def test_a_directory_named_like_the_root_marker_is_skipped_not_fatal() -> None:
    """One unspellable directory costs its own component, not the whole view."""
    from repowise.server.services.c4_builder.components import _bucket_into_components

    class _Node:
        def __init__(self, node_id: str) -> None:
            self.node_id = node_id
            self.symbol_count = 1
            self.language = "python"

    components, file_index = _bucket_into_components(
        [_Node("pkg/odd#root/a.py"), _Node("pkg/fine/b.py")],
        "pkg",
        "(root)",
    )
    names = {c.name for c in components}
    assert "fine" in names
    assert not any("#root" in c.path for c in components)
    assert "pkg/odd#root/a.py" not in file_index


def test_the_batched_and_per_container_paths_agree_on_a_nested_container() -> None:
    """A container inside another is where the two ownership rules could drift.

    ``detect_components`` rejects a file matching any sibling root's prefix, so
    with containers ``apps`` and ``apps/web`` a file under ``apps/web`` is
    claimed by neither. That is a real gap, but the batched path's contract is
    to agree with the per-container one element for element — so it must
    reproduce it rather than quietly improving on it.
    """
    from repowise.server.services.c4_builder.components import _matching_roots, _owned_by

    roots = ["apps", "apps/web"]
    for node_id in ("apps/x.ts", "apps/web/y.ts", "other/z.ts"):
        batched = _matching_roots(node_id, roots)
        for root in roots:
            siblings = tuple(r + "/" for r in roots if r != root)
            per_container = _owned_by(node_id, root, siblings)
            assert per_container is (batched == [root]), (node_id, root)
