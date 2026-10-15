"""The ``dependencies`` and ``layers`` datasets: how the code is put together.

``dependencies`` is every package-to-package edge of the container view (the
roll-up get_overview's dependency block shows its top ten of); ``layers`` is
the architecture layers get_overview lists, in stack order.
"""

from __future__ import annotations

import json
from typing import Any

from repowise.server.chat_datasets import (
    ARROW,
    SUMS,
    DatasetField,
    LoadContext,
    register_dataset,
)


@register_dataset(
    "dependencies",
    description="One row per package-to-package dependency edge (get_overview's dependencies).",
    fields={
        "edge": DatasetField("text", f"the edge as 'from {ARROW} to', for labels"),
        "from_package": DatasetField("text", "the package that depends"),
        "to_package": DatasetField("text", "the package it depends on"),
        "weight": DatasetField("number", "file pairs behind the edge (test files add none)", SUMS),
        "coupling": DatasetField("text", "loose, moderate or tight"),
    },
    basis=("inferred", "import graph"),
    example={"x": "edge", "y": "weight", "top": 10},
    noun="dependency edges",
)
async def load_dependencies(ctx: LoadContext) -> list[dict[str, Any]]:
    from repowise.server.mcp_server._helpers import _get_exclude_spec
    from repowise.server.services.c4_builder import container_dependencies

    containers, relations = await container_dependencies(
        ctx.session, ctx.repository.id, exclude_spec=_get_exclude_spec(ctx.repo_path)
    )
    path_of = {c.id: c.path or "." for c in containers}
    return [
        {
            "edge": f"{path_of[r.source_id]} {ARROW} {path_of[r.target_id]}",
            "from_package": path_of[r.source_id],
            "to_package": path_of[r.target_id],
            "weight": r.edge_count,
            "coupling": r.coupling,
        }
        for r in relations
        if r.source_id in path_of and r.target_id in path_of
    ]


@register_dataset(
    "layers",
    description="One row per architecture layer, in stack order (get_overview's layers).",
    fields={
        "layer": DatasetField("text", "the layer's name"),
        "files": DatasetField("number", "files in the layer", SUMS, unit="files"),
        "order": DatasetField("number", "the layer's place in the stack, top first", ("min",)),
    },
    basis=("inferred", "knowledge graph"),
    example={"x": "layer", "y": "files"},
    noun="layers",
)
async def load_layers(ctx: LoadContext) -> list[dict[str, Any]]:
    from repowise.core.generation.layers import is_adjacent_layer
    from repowise.core.persistence.crud.knowledge_graph import get_kg_layers

    layers = [
        layer
        for layer in await get_kg_layers(ctx.session, ctx.repository.id)
        if not is_adjacent_layer(layer.layer_id)
    ]
    return [
        {
            "layer": layer.name,
            "files": len(json.loads(layer.node_ids_json) if layer.node_ids_json else []),
            "order": i + 1,
        }
        for i, layer in enumerate(layers)
    ]
