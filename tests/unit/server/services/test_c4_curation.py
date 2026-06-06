"""C4 legibility: curated KG layers feed the architecture view, and the L1/L2
Mermaid groups externals by category once there are many (plan §Phase 5).

Also pins the Phase-A data contract of the viewer plan: every curated field
(sub-groups, layer-aware tour, entry points, node summaries/tags/types)
survives JSON → DB → builder with zero loss, and uncurated repos behave
exactly as before."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from repowise.core.persistence import (
    batch_upsert_graph_nodes,
    upsert_repository,
)
from repowise.server.services.c4_builder.architecture import (
    _layers_from_db,
    _layers_from_knowledge_graph,
    _migrate_kg_file_to_db,
    _tour_from_knowledge_graph,
    build_architecture_view,
)
from repowise.server.services.c4_builder.mermaid import to_mermaid_l1
from repowise.server.services.c4_builder.models import (
    C4L1,
    ExternalSystemView,
    Person,
    Relation,
    System,
)

# ---------------------------------------------------------------------------
# Curated layers flow through the architecture cascade (tier 2: KG file)
# ---------------------------------------------------------------------------


def test_architecture_view_consumes_curated_layers():
    kg = {
        "layers": [
            {
                "id": "layer:ui",
                "name": "UI",
                "description": "front end",
                "nodeIds": ["file:src/ui/a.tsx", "file:src/ui/b.tsx"],
            },
            {
                "id": "layer:service",
                "name": "Service",
                "description": "core",
                "nodeIds": ["file:src/core/x.py"],
            },
        ]
    }
    node_ids = {"src/ui/a.tsx", "src/ui/b.tsx", "src/core/x.py"}
    layers = _layers_from_knowledge_graph(kg, node_ids)

    # Curated names/ids/order preserved — not community-N / cluster-N.
    assert [layer["name"] for layer in layers] == ["UI", "Service"]
    assert [layer["id"] for layer in layers] == ["layer:ui", "layer:service"]
    assert layers[0]["node_ids"] == ["src/ui/a.tsx", "src/ui/b.tsx"]


def test_layers_carry_sub_groups_and_display_order():
    kg = {
        "layers": [
            {
                "id": "layer:ui",
                "name": "UI",
                "nodeIds": ["file:src/ui/a.tsx", "file:src/ui/b.tsx"],
                "display_order": 3,
                "subGroups": [
                    {"id": "layer:ui:forms", "name": "forms", "nodeIds": ["file:src/ui/a.tsx"]},
                    {"id": "layer:ui:gone", "name": "gone", "nodeIds": ["file:src/ui/zz.tsx"]},
                ],
            },
        ]
    }
    layers = _layers_from_knowledge_graph(kg, {"src/ui/a.tsx", "src/ui/b.tsx"})

    assert layers[0]["display_order"] == 3
    # Sub-group node ids are prefix-stripped; groups with no surviving nodes drop.
    assert layers[0]["sub_groups"] == [
        {"id": "layer:ui:forms", "name": "forms", "node_ids": ["src/ui/a.tsx"]},
    ]


def test_layers_from_db_tolerate_pre_migration_rows():
    """Rows from a DB created before migration 0030 lack the new columns."""
    row = SimpleNamespace(
        layer_id="layer:ui",
        name="UI",
        description="",
        node_ids_json=json.dumps(["file:src/ui/a.tsx"]),
    )
    layers = _layers_from_db([row], {"src/ui/a.tsx"})
    assert layers[0]["sub_groups"] == []
    assert layers[0]["display_order"] == 0


def test_tour_steps_carry_curated_fields():
    kg = {
        "tour": [
            {
                "order": 1,
                "title": "main.py",
                "target_path": "src/main.py",
                "page_type": "file_page",
                "depth": 0,
                "kind": "code",
                "reason": "Top of the stack.",
                "layer_id": "layer:cli",
            },
            # Legacy LLM step: nodeIds, no curated fields.
            {"order": 2, "title": "Core", "description": "d", "nodeIds": ["file:core.py"]},
        ]
    }
    steps = _tour_from_knowledge_graph(kg)

    curated = steps[0]
    assert curated.target_path == "src/main.py"
    assert curated.layer_id == "layer:cli"
    assert curated.reason == "Top of the stack."
    assert curated.depth == 0
    assert curated.kind == "code"
    assert curated.page_type == "file_page"
    # Curated steps address one file: node_ids falls back to the target path.
    assert curated.node_ids == ["src/main.py"]

    legacy = steps[1]
    assert legacy.node_ids == ["core.py"]
    assert legacy.target_path is None
    assert legacy.kind == ""


# ---------------------------------------------------------------------------
# Round trip: curated JSON → auto-migrate → DB → architecture view (zero loss)
# ---------------------------------------------------------------------------

_CURATED_KG = {
    "version": "1.0.0",
    "project": {
        "name": "demo",
        "entry_points": ["src/main.py"],
        "entry_candidates": ["src/main.py", "src/api/routes.py", "missing/file.py"],
    },
    "nodes": [
        {
            "id": "file:src/main.py",
            "filePath": "src/main.py",
            "type": "file",
            "summary": "SENTINEL: CLI entry point wiring commands.",
            "tags": ["entry_point", "python"],
        },
        {
            "id": "file:src/api/routes.py",
            "filePath": "src/api/routes.py",
            "type": "file",
            "summary": "SENTINEL: HTTP routes.",
            "tags": ["python"],
        },
        {
            "id": "file:src/ui/form.tsx",
            "filePath": "src/ui/form.tsx",
            "type": "file",
            "summary": "SENTINEL: form component.",
            "tags": ["typescript"],
        },
        {
            "id": "file:Dockerfile",
            "filePath": "Dockerfile",
            "type": "service",
            "summary": "SENTINEL: container build.",
            "tags": ["infra"],
        },
        {"id": "concept:auth", "type": "concept", "summary": "not file meta"},
    ],
    "edges": [],
    "layers": [
        {
            "id": "layer:ui",
            "name": "UI",
            "description": "Presentation",
            "nodeIds": ["file:src/ui/form.tsx"],
            "display_order": 0,
            "subGroups": [
                {"id": "layer:ui:forms", "name": "forms", "nodeIds": ["file:src/ui/form.tsx"]},
            ],
        },
        {
            "id": "layer:api",
            "name": "API",
            "description": "Routes",
            "nodeIds": ["file:src/api/routes.py", "file:src/main.py"],
            "display_order": 1,
        },
        {
            "id": "layer:config",
            "name": "Config",
            "description": "Infra",
            "nodeIds": ["file:Dockerfile"],
            "display_order": 2,
        },
    ],
    "tour": [
        {
            "order": 1,
            "title": "main.py",
            "target_path": "src/main.py",
            "page_type": "file_page",
            "depth": 0,
            "kind": "code",
            "reason": "SENTINEL: start of the control flow.",
            "layer_id": "layer:api",
        },
    ],
}


async def _seed_curated_repo(session, tmp_path):
    kg_dir = tmp_path / ".repowise"
    kg_dir.mkdir()
    (kg_dir / "knowledge-graph.json").write_text(json.dumps(_CURATED_KG))

    repo = await upsert_repository(session, name="demo", local_path=str(tmp_path))
    files = ["src/main.py", "src/api/routes.py", "src/ui/form.tsx", "Dockerfile"]
    await batch_upsert_graph_nodes(
        session,
        repo.id,
        [
            {
                "node_id": f,
                "node_type": "file",
                "language": "python" if f.endswith(".py") else "",
                "symbol_count": 1,
            }
            for f in files
        ],
    )
    return repo


def _assert_curated_view(view) -> None:
    by_id = {layer.id: layer for layer in view.layers}
    ui = by_id["layer:ui"]
    assert ui.display_order == 0
    assert [(sg.id, sg.name, sg.node_ids) for sg in ui.sub_groups] == [
        ("layer:ui:forms", "forms", ["src/ui/form.tsx"]),
    ]
    assert by_id["layer:api"].display_order == 1
    assert by_id["layer:api"].sub_groups == []

    step = view.tour[0]
    assert step.target_path == "src/main.py"
    assert step.layer_id == "layer:api"
    assert step.reason == "SENTINEL: start of the control flow."
    assert step.depth == 0
    assert step.kind == "code"
    assert step.page_type == "file_page"
    assert step.node_ids == ["src/main.py"]

    assert view.entry_points == ["src/main.py"]
    # Candidates are filtered to nodes that exist in the graph.
    assert view.entry_candidates == ["src/main.py", "src/api/routes.py"]

    nodes = {n.id: n for n in view.nodes}
    assert nodes["src/main.py"].summary == "SENTINEL: CLI entry point wiring commands."
    assert nodes["src/main.py"].tags == ["entry_point", "python"]
    assert nodes["Dockerfile"].node_type == "service"
    assert nodes["Dockerfile"].summary == "SENTINEL: container build."


async def test_round_trip_zero_curated_field_loss(async_session, tmp_path):
    """First read auto-migrates the file; second read serves from the DB.

    Both views must carry every curated field — the Phase-A acceptance."""
    repo = await _seed_curated_repo(async_session, tmp_path)

    view_from_file = await build_architecture_view(async_session, repo.id)
    _assert_curated_view(view_from_file)

    # Remove the workspace file: the second read must come purely from the DB.
    os.remove(tmp_path / ".repowise" / "knowledge-graph.json")
    view_from_db = await build_architecture_view(async_session, repo.id)
    _assert_curated_view(view_from_db)


async def test_migration_twice_is_idempotent(async_session, tmp_path):
    """Two first-readers racing into the migration must be idempotent.

    The delete-then-insert migration carries no concurrency guard, so a
    concurrent first-reader can hit the unique constraints. Running it twice in
    a row (the serialized analogue of that race) must raise nothing and leave
    the DB holding exactly one curated set."""
    repo = await _seed_curated_repo(async_session, tmp_path)

    # Both calls open their own sessions and commit; neither raises.
    await _migrate_kg_file_to_db(async_session, repo.id, _CURATED_KG)
    await _migrate_kg_file_to_db(async_session, repo.id, _CURATED_KG)

    # The view (served from the migrated DB rows) carries every curated field.
    os.remove(tmp_path / ".repowise" / "knowledge-graph.json")
    view = await build_architecture_view(async_session, repo.id)
    _assert_curated_view(view)


async def test_failed_migration_does_not_roll_back_caller_writes(async_session, tmp_path):
    """A failed migration runs in its own session, so the caller's pending
    writes survive — the old broad ``except: session.rollback()`` would have
    discarded them."""
    repo = await _seed_curated_repo(async_session, tmp_path)

    # An unrelated write pending on the CALLER's session.
    sentinel = await upsert_repository(
        session=async_session, name="caller-sentinel", local_path=str(tmp_path / "x")
    )

    # A KG whose layer is missing the required "id" key makes upsert_kg_layers
    # raise inside the migration's own session.
    broken_kg = {"layers": [{"name": "no id key"}]}
    with pytest.raises(KeyError):
        await _migrate_kg_file_to_db(async_session, repo.id, broken_kg)

    # The caller's session was never touched by the migration's rollback.
    await async_session.flush()
    found = await async_session.get(type(sentinel), sentinel.id)
    assert found is not None
    assert found.name == "caller-sentinel"


async def test_uncurated_repo_unchanged(async_session, tmp_path):
    """Flag-off contract: no KG file, no DB rows → behaviour identical to today."""
    repo = await upsert_repository(session=async_session, name="plain", local_path=str(tmp_path))
    await batch_upsert_graph_nodes(
        async_session,
        repo.id,
        [{"node_id": "src/a.py", "node_type": "file", "language": "python", "symbol_count": 1}],
    )

    view = await build_architecture_view(async_session, repo.id)

    assert view.entry_points == []
    assert view.entry_candidates == []
    assert all(layer.sub_groups == [] for layer in view.layers)
    node = view.nodes[0]
    assert node.summary == "Handles src logic"  # heuristic fallback intact
    assert node.node_type == "file"


def _ext(name: str, category: str) -> ExternalSystemView:
    return ExternalSystemView(
        id=f"ext:{name}",
        name=name,
        display_name=name,
        category=category,
        ecosystem="pypi",
        version="",
    )


def _l1(externals: list[ExternalSystemView]) -> C4L1:
    system = System(id="sys:r", name="r")
    return C4L1(
        system=system,
        people=[Person(id="person:user", name="User", description="")],
        external_systems=externals,
        relations=[
            Relation(source_id=system.id, target_id=e.id, label=e.category) for e in externals
        ],
    )


# ---------------------------------------------------------------------------
# Mermaid external grouping
# ---------------------------------------------------------------------------


def test_few_externals_stay_flat():
    externals = [_ext(f"lib{i}", "library") for i in range(4)]
    out = to_mermaid_l1(_l1(externals))
    assert "Boundary(extgrp_" not in out


def test_many_externals_group_by_category():
    externals = (
        [_ext(f"fw{i}", "framework") for i in range(4)]
        + [_ext(f"svc{i}", "service") for i in range(3)]
        + [_ext(f"lib{i}", "library") for i in range(5)]
    )
    out = to_mermaid_l1(_l1(externals))
    assert "Boundary(extgrp_framework" in out
    assert "Boundary(extgrp_service" in out
    assert "Boundary(extgrp_library" in out
    assert '"Frameworks"' in out
    assert '"Services & Infrastructure"' in out
    # Frameworks group is rendered before Libraries (category priority order).
    assert out.index("extgrp_framework") < out.index("extgrp_library")
    # Every external still appears as a box.
    for i in range(5):
        assert f"ext_lib{i}" in out
