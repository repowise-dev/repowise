"""The ``dependencies`` and ``layers`` datasets: how the code is put together.

``dependencies`` is every package-to-package edge of the container view (the
roll-up get_overview's dependency block shows its top ten of); ``layers`` is
the architecture layers get_overview lists, in stack order.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from repowise.core.analysis.health.rows import field
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
    return dependencies_rows(containers, relations)


def dependencies_rows(containers: Iterable[Any], relations: Iterable[Any]) -> list[dict[str, Any]]:
    """The ``dependencies`` dataset's rows over the container view's boxes and edges.

    *containers* hold ``id`` and ``path``; *relations* hold ``source_id``,
    ``target_id``, ``edge_count`` and ``coupling``. An edge to a box not in
    *containers* is left out.
    """
    path_of = {field(c, "id"): field(c, "path") or "." for c in containers}
    rows = []
    for r in relations:
        source, target = field(r, "source_id"), field(r, "target_id")
        if source in path_of and target in path_of:
            rows.append(
                {
                    "edge": f"{path_of[source]} {ARROW} {path_of[target]}",
                    "from_package": path_of[source],
                    "to_package": path_of[target],
                    "weight": field(r, "edge_count"),
                    "coupling": field(r, "coupling"),
                }
            )
    return rows


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
    from repowise.core.persistence.crud.knowledge_graph import get_kg_layers

    return layers_rows(await get_kg_layers(ctx.session, ctx.repository.id))


def layers_rows(layers: Iterable[Any]) -> list[dict[str, Any]]:
    """The ``layers`` dataset's rows over knowledge-graph layers, in the order given.

    Each record holds ``layer_id``, ``name`` and ``node_ids_json``; adjacent
    layers (outside the stack) are left out.
    """
    from repowise.core.generation.layers import is_adjacent_layer

    stack = [layer for layer in layers if not is_adjacent_layer(field(layer, "layer_id"))]
    rows = []
    for i, layer in enumerate(stack):
        node_ids = field(layer, "node_ids_json")
        rows.append(
            {
                "layer": field(layer, "name"),
                "files": len(json.loads(node_ids) if node_ids else []),
                "order": i + 1,
            }
        )
    return rows
