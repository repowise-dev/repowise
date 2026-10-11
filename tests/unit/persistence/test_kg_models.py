"""Tests for knowledge graph layer and tour step persistence."""

from __future__ import annotations

import json

import pytest

from repowise.core.persistence.crud import (
    get_kg_layers,
    get_kg_node_meta,
    get_kg_project_meta,
    get_kg_tour_steps,
    upsert_kg_layers,
    upsert_kg_node_meta,
    upsert_kg_project_meta,
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


# ---------------------------------------------------------------------------
# Curated fields (sub-groups, layer-aware tour, project meta, node meta)
# ---------------------------------------------------------------------------


async def test_kg_layers_sub_groups_round_trip(async_session, repo):
    """Curated subGroups survive the JSON → DB boundary verbatim."""
    sub_groups = [
        {"id": "layer:ui:forms", "name": "forms", "nodeIds": ["file:src/ui/form.tsx"]},
        {"id": "layer:ui:tables", "name": "tables", "nodeIds": ["file:src/ui/table.tsx"]},
    ]
    await upsert_kg_layers(
        async_session, repo.id,
        [{"id": "layer:ui", "name": "UI", "nodeIds": [], "subGroups": sub_groups}],
    )
    result = await get_kg_layers(async_session, repo.id)
    assert json.loads(result[0].sub_groups_json) == sub_groups


async def test_kg_layers_sub_groups_default_empty(async_session, repo):
    """Layers without subGroups (legacy / community layers) store []."""
    await upsert_kg_layers(
        async_session, repo.id,
        [{"id": "layer:plain", "name": "Plain", "nodeIds": []}],
    )
    result = await get_kg_layers(async_session, repo.id)
    assert json.loads(result[0].sub_groups_json) == []


async def test_kg_layers_sub_groups_snake_case_variant(async_session, repo):
    await upsert_kg_layers(
        async_session, repo.id,
        [{"id": "layer:s", "name": "S", "nodeIds": [],
          "sub_groups": [{"id": "layer:s:x", "name": "x", "nodeIds": []}]}],
    )
    result = await get_kg_layers(async_session, repo.id)
    assert json.loads(result[0].sub_groups_json)[0]["name"] == "x"


async def test_kg_tour_steps_curated_fields_round_trip(async_session, repo):
    """The six curated tour fields survive the JSON → DB boundary."""
    steps = [
        {
            "order": 1,
            "title": "main.py",
            "target_path": "src/main.py",
            "page_type": "file_page",
            "depth": 0,
            "kind": "code",
            "reason": "Top of the stack (CLI) — start of the control flow.",
            "layer_id": "layer:cli",
        },
    ]
    await upsert_kg_tour_steps(async_session, repo.id, steps)
    result = await get_kg_tour_steps(async_session, repo.id)
    row = result[0]
    assert row.target_path == "src/main.py"
    assert row.page_type == "file_page"
    assert row.depth == 0
    assert row.kind == "code"
    assert row.reason == "Top of the stack (CLI) — start of the control flow."
    assert row.layer_id == "layer:cli"


async def test_kg_tour_steps_legacy_steps_get_defaults(async_session, repo):
    """Legacy LLM tour steps (no curated fields) store None/empty defaults."""
    await upsert_kg_tour_steps(
        async_session, repo.id,
        [{"order": 1, "title": "Legacy", "description": "d", "nodeIds": ["file:a.py"]}],
    )
    row = (await get_kg_tour_steps(async_session, repo.id))[0]
    assert row.target_path is None
    assert row.layer_id is None
    assert row.reason == ""
    assert row.depth is None
    assert row.kind == ""
    assert row.page_type is None


async def test_kg_project_meta_round_trip(async_session, repo):
    await upsert_kg_project_meta(
        async_session, repo.id,
        entry_points=["src/main.py", "src/cli.py"],
        entry_candidates=["src/main.py", "src/cli.py", "src/app.py"],
    )
    meta = await get_kg_project_meta(async_session, repo.id)
    assert meta is not None
    assert json.loads(meta.entry_points_json) == ["src/main.py", "src/cli.py"]
    assert json.loads(meta.entry_candidates_json) == [
        "src/main.py", "src/cli.py", "src/app.py",
    ]


async def test_kg_project_meta_replaces_on_reinit(async_session, repo):
    """One row per repo: re-upsert replaces, never duplicates."""
    await upsert_kg_project_meta(async_session, repo.id, entry_points=["old.py"])
    await upsert_kg_project_meta(async_session, repo.id, entry_points=["new.py"])
    meta = await get_kg_project_meta(async_session, repo.id)
    assert json.loads(meta.entry_points_json) == ["new.py"]


async def test_kg_project_meta_missing_returns_none(async_session, repo):
    assert await get_kg_project_meta(async_session, repo.id) is None


async def test_kg_node_meta_round_trip(async_session, repo):
    nodes = [
        {"id": "src/main.py", "type": "file", "summary": "CLI entry point.",
         "tags": ["entry_point", "python"]},
        {"id": "Dockerfile", "type": "service", "summary": "Container build.", "tags": []},
    ]
    await upsert_kg_node_meta(async_session, repo.id, nodes)
    rows = {r.node_id: r for r in await get_kg_node_meta(async_session, repo.id)}
    assert rows["src/main.py"].summary == "CLI entry point."
    assert json.loads(rows["src/main.py"].tags_json) == ["entry_point", "python"]
    assert rows["Dockerfile"].node_type == "service"


async def test_kg_node_meta_replaces_on_reinit(async_session, repo):
    await upsert_kg_node_meta(async_session, repo.id, [{"id": "old.py", "summary": "x"}])
    await upsert_kg_node_meta(async_session, repo.id, [{"id": "new.py", "summary": "y"}])
    rows = await get_kg_node_meta(async_session, repo.id)
    assert len(rows) == 1
    assert rows[0].node_id == "new.py"
    assert rows[0].node_type == "file"  # default


async def test_kg_node_meta_empty_for_new_repo(async_session, repo):
    assert await get_kg_node_meta(async_session, repo.id) == []
