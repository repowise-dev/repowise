"""persist_generation carries curated KG meta (project entry points, node
summaries/tags/types) into the DB alongside layers and tour steps."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from repowise.core.persistence.crud import (
    get_kg_layers,
    get_kg_node_meta,
    get_kg_project_meta,
    get_kg_tour_steps,
)
from repowise.core.pipeline.persist import persist_generation
from tests.unit.persistence.helpers import insert_repo


@pytest.fixture
async def repo(async_session):
    return await insert_repo(async_session)


def _result(**kg_fields) -> SimpleNamespace:
    defaults: dict = {"project": {}, "nodes": [], "layers": [], "tour": []}
    kg = SimpleNamespace(**{**defaults, **kg_fields})
    return SimpleNamespace(generated_pages=None, knowledge_graph_result=kg)


async def test_persists_entry_points_from_project(async_session, repo):
    result = _result(
        project={
            "name": "demo",
            "entry_points": ["src/main.py"],
            "entry_candidates": ["src/main.py", "src/app.py"],
        }
    )
    await persist_generation(result, async_session, repo.id)

    meta = await get_kg_project_meta(async_session, repo.id)
    assert meta is not None
    assert json.loads(meta.entry_points_json) == ["src/main.py"]
    assert json.loads(meta.entry_candidates_json) == ["src/main.py", "src/app.py"]


async def test_persists_file_node_meta_with_prefix_stripped(async_session, repo):
    result = _result(
        nodes=[
            {
                "id": "file:src/main.py",
                "type": "file",
                "summary": "CLI entry point.",
                "tags": ["entry_point", "python"],
            },
            {"id": "file:Dockerfile", "type": "service", "summary": "Container build."},
            # Non-file nodes (concepts, symbols) are not node-meta material.
            {"id": "concept:auth", "type": "concept", "summary": "Auth concept."},
        ]
    )
    await persist_generation(result, async_session, repo.id)

    rows = {r.node_id: r for r in await get_kg_node_meta(async_session, repo.id)}
    assert set(rows) == {"src/main.py", "Dockerfile"}
    assert rows["src/main.py"].summary == "CLI entry point."
    assert json.loads(rows["src/main.py"].tags_json) == ["entry_point", "python"]
    assert rows["Dockerfile"].node_type == "service"


async def test_no_curated_meta_writes_nothing(async_session, repo):
    """Uncurated KG (no entry_points, no nodes) leaves the meta tables empty."""
    await persist_generation(_result(), async_session, repo.id)

    assert await get_kg_project_meta(async_session, repo.id) is None
    assert await get_kg_node_meta(async_session, repo.id) == []


async def test_layers_and_tour_still_persisted(async_session, repo):
    """Existing layer/tour persistence is unchanged by the meta additions."""
    result = _result(
        layers=[{"id": "layer:cli", "name": "CLI", "nodeIds": ["file:src/main.py"]}],
        tour=[
            {
                "order": 1,
                "title": "main.py",
                "target_path": "src/main.py",
                "kind": "code",
                "reason": "Top of the stack.",
                "layer_id": "layer:cli",
            }
        ],
    )
    await persist_generation(result, async_session, repo.id)

    layers = await get_kg_layers(async_session, repo.id)
    assert [layer.layer_id for layer in layers] == ["layer:cli"]
    steps = await get_kg_tour_steps(async_session, repo.id)
    assert steps[0].target_path == "src/main.py"
    assert steps[0].layer_id == "layer:cli"


async def test_missing_kg_result_is_a_noop(async_session, repo):
    result = SimpleNamespace(generated_pages=None, knowledge_graph_result=None)
    await persist_generation(result, async_session, repo.id)
    assert await get_kg_layers(async_session, repo.id) == []
    assert await get_kg_project_meta(async_session, repo.id) is None
