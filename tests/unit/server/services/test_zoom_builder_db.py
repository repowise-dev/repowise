"""build_zoom_map over a seeded graph, with the repository's manifests on disk."""

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
from repowise.core.persistence.models import KnowledgeGraphProjectMeta
from repowise.server.services.zoom_builder import build_zoom_map
from repowise.server.services.zoom_builder.tree import file_id


async def _seed(session, checkout):
    """A root CLI package and a nested worker package that reaches a database."""
    (checkout / "pyproject.toml").write_text('[project]\nname = "acme-tool"\n')
    (checkout / "worker").mkdir()
    (checkout / "worker" / "pyproject.toml").write_text('[project]\nname = "acme-worker"\n')
    repo = await upsert_repository(session, name="acme", local_path=str(checkout))
    files = {
        "pyproject.toml": "toml",
        "src/acme/cli/main.py": "python",
        "src/acme/core/engine.py": "python",
        "worker/pyproject.toml": "toml",
        "worker/tasks/run_jobs.py": "python",
        "worker/store.py": "python",
    }
    nodes = [
        {"node_id": path, "node_type": "file", "language": lang, "symbol_count": 1}
        for path, lang in files.items()
    ]
    nodes.append({"node_id": "external:sqlalchemy.orm", "node_type": "file", "language": "python"})
    await batch_upsert_graph_nodes(session, repo.id, nodes)
    edges = [
        ("src/acme/cli/main.py", "src/acme/core/engine.py"),
        ("worker/tasks/run_jobs.py", "worker/store.py"),
        ("worker/store.py", "external:sqlalchemy.orm"),
    ]
    await batch_upsert_graph_edges(
        session,
        repo.id,
        [{"source_node_id": s, "target_node_id": t, "edge_type": "imports"} for s, t in edges],
    )
    id_map = await bulk_upsert_external_systems(
        session,
        repo.id,
        [
            {
                "name": "sqlalchemy",
                "display_name": "SQLAlchemy",
                "ecosystem": "pypi",
                "category": "service",
                "io_kind": "db",
                "declared_in": "worker/pyproject.toml",
                "is_dev_dep": False,
            }
        ],
    )
    await link_graph_nodes_to_external_systems(
        session, repo.id, {name: sid for (name, _), sid in id_map.items()}
    )
    session.add(
        KnowledgeGraphProjectMeta(
            repository_id=repo.id,
            entry_points_json=json.dumps(["src/acme/cli/main.py", "worker/tasks/run_jobs.py"]),
        )
    )
    await session.commit()
    return repo


@pytest.mark.asyncio
async def test_build_zoom_map_serves_containers_externals_and_technologies(async_session, tmp_path):
    repo = await _seed(async_session, tmp_path)
    zoom = await build_zoom_map(async_session, repo.id)

    by_path = {c.path: c for c in zoom.containers}
    assert set(by_path) == {"", "worker"}
    # Named from the manifests on disk, kinds from the curated entry points.
    assert (by_path[""].name, by_path[""].kind) == ("acme-tool", "cli")
    assert (by_path["worker"].name, by_path["worker"].kind) == ("acme-worker", "worker")
    assert by_path["worker"].technologies[0] == "Python"

    assert [e.display_name for e in zoom.externals] == ["SQLAlchemy"]
    assert {r.target_id for r in zoom.external_relations} == {zoom.externals[0].id}
    assert zoom.nodes[file_id("worker/store.py")].container_ids == ("pkg:worker",)
    assert "Python" in zoom.nodes[zoom.root_id].technologies
