"""Tests for knowledge graph layer and tour step persistence."""

from __future__ import annotations

import json

import pytest

from repowise.core.persistence.crud import (
    get_kg_layers,
    get_kg_tour_steps,
    upsert_kg_layers,
    upsert_kg_tour_steps,
)
from tests.unit.persistence.helpers import insert_repo


@pytest.fixture
async def repo(async_session):
    return await insert_repo(async_session)


async def test_upsert_kg_layers_creates_layers(async_session, repo):
    layers = [
        {"id": "layer:cli", "name": "CLI", "description": "Command line", "nodeIds": ["file:main.py"]},
        {"id": "layer:core", "name": "Core", "description": "Core logic", "nodeIds": ["file:core.py"]},
    ]
    await upsert_kg_layers(async_session, repo.id, layers)
    result = await get_kg_layers(async_session, repo.id)
    assert len(result) == 2
    assert result[0].name == "CLI"
    assert result[1].name == "Core"
    assert json.loads(result[0].node_ids_json) == ["file:main.py"]


async def test_upsert_kg_layers_replaces_on_reinit(async_session, repo):
    """Verify delete-then-insert: old layers don't persist."""
    await upsert_kg_layers(async_session, repo.id, [{"id": "layer:old", "name": "Old", "nodeIds": []}])
    await upsert_kg_layers(async_session, repo.id, [{"id": "layer:new", "name": "New", "nodeIds": []}])
    result = await get_kg_layers(async_session, repo.id)
    assert len(result) == 1
    assert result[0].layer_id == "layer:new"


async def test_upsert_kg_layers_display_order(async_session, repo):
    """Layers preserve insertion order via display_order."""
    layers = [
        {"id": "layer:b", "name": "B", "nodeIds": []},
        {"id": "layer:a", "name": "A", "nodeIds": []},
    ]
    await upsert_kg_layers(async_session, repo.id, layers)
    result = await get_kg_layers(async_session, repo.id)
    assert result[0].name == "B"
    assert result[0].display_order == 0
    assert result[1].name == "A"
    assert result[1].display_order == 1


async def test_upsert_kg_layers_node_ids_key_variants(async_session, repo):
    """Accepts both 'nodeIds' (camelCase) and 'node_ids' (snake_case)."""
    layers = [
        {"id": "layer:camel", "name": "Camel", "nodeIds": ["file:a.py"]},
        {"id": "layer:snake", "name": "Snake", "node_ids": ["file:b.py"]},
    ]
    await upsert_kg_layers(async_session, repo.id, layers)
    result = await get_kg_layers(async_session, repo.id)
    assert json.loads(result[0].node_ids_json) == ["file:a.py"]
    assert json.loads(result[1].node_ids_json) == ["file:b.py"]


async def test_upsert_kg_tour_steps(async_session, repo):
    steps = [
        {"order": 1, "title": "Entry Point", "description": "Start here", "nodeIds": ["file:main.py"]},
        {"order": 2, "title": "Core Logic", "description": "Then here", "nodeIds": ["file:core.py"]},
    ]
    await upsert_kg_tour_steps(async_session, repo.id, steps)
    result = await get_kg_tour_steps(async_session, repo.id)
    assert len(result) == 2
    assert result[0].title == "Entry Point"
    assert result[1].step_order == 2
    assert json.loads(result[0].node_ids_json) == ["file:main.py"]


async def test_upsert_kg_tour_steps_replaces(async_session, repo):
    """Tour steps replaced on re-init."""
    await upsert_kg_tour_steps(
        async_session, repo.id,
        [{"order": 1, "title": "Old", "nodeIds": []}],
    )
    await upsert_kg_tour_steps(
        async_session, repo.id,
        [{"order": 1, "title": "New", "nodeIds": []}],
    )
    result = await get_kg_tour_steps(async_session, repo.id)
    assert len(result) == 1
    assert result[0].title == "New"


async def test_get_kg_layers_empty_for_new_repo(async_session, repo):
    """Graceful degradation: no KG data returns empty list."""
    result = await get_kg_layers(async_session, repo.id)
    assert result == []


async def test_get_kg_tour_steps_empty_for_new_repo(async_session, repo):
    result = await get_kg_tour_steps(async_session, repo.id)
    assert result == []


async def test_kg_layers_description_defaults_empty(async_session, repo):
    """Description defaults to empty string when not provided."""
    await upsert_kg_layers(
        async_session, repo.id,
        [{"id": "layer:minimal", "name": "Minimal", "nodeIds": []}],
    )
    result = await get_kg_layers(async_session, repo.id)
    assert result[0].description == ""


async def test_kg_tour_steps_description_defaults_empty(async_session, repo):
    await upsert_kg_tour_steps(
        async_session, repo.id,
        [{"order": 1, "title": "No desc", "nodeIds": []}],
    )
    result = await get_kg_tour_steps(async_session, repo.id)
    assert result[0].description == ""
