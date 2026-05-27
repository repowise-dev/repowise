"""Knowledge-graph layer / tour / role helpers for get_context."""

from __future__ import annotations

import json
from typing import Any


def _find_layer_for_file(path: str, layers: list) -> Any | None:
    for layer in layers:
        node_ids = getattr(layer, "_parsed_node_ids", None)
        if node_ids is None:
            node_ids = json.loads(layer.node_ids_json) if layer.node_ids_json else []
        if f"file:{path}" in node_ids or path in node_ids:
            return layer
    return None


def _find_tour_step_for_file(path: str, steps: list) -> Any | None:
    for step in steps:
        node_ids = getattr(step, "_parsed_node_ids", None)
        if node_ids is None:
            node_ids = json.loads(step.node_ids_json) if step.node_ids_json else []
        if f"file:{path}" in node_ids or path in node_ids:
            return step
    return None


def _classify_file_role(path: str, layer: Any, incoming_edges: list) -> str:
    raw = getattr(layer, "_parsed_node_ids", None)
    if raw is None:
        raw = json.loads(layer.node_ids_json) if layer.node_ids_json else []
    node_ids = set(raw)
    cross_layer = [
        e
        for e in incoming_edges
        if f"file:{e.source_node_id}" not in node_ids and e.source_node_id not in node_ids
    ]
    if cross_layer:
        return "edge_connector"
    if not incoming_edges:
        return "entry_point"
    return "internal"
