"""Graph-derived overview blocks: code communities and architecture layers."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.crud import (
    get_kg_layers as _get_kg_layers,
)
from repowise.core.persistence.crud import (
    get_kg_tour_steps as _get_kg_tour_steps,
)
from repowise.core.persistence.models import GraphNode
from repowise.server.mcp_server._helpers import filter_graph_nodes


async def _load_community_nodes(
    session: Any, repository: Any, exclude_spec: Any, all_git: list
) -> list[GraphNode]:
    """File nodes for community grouping; widened to all non-test nodes when git data exists."""
    if not all_git:
        node_result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
                GraphNode.node_type == "file",
            )
        )
    else:
        node_result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
                GraphNode.is_test == False,  # noqa: E712
            )
        )
    return filter_graph_nodes(list(node_result.scalars().all()), exclude_spec)


def _community_display_label(
    label: str, members: list[GraphNode], cid: int, generic_labels: set[str]
) -> str:
    """Use the heuristic label, or the dominant specific directory when it's generic."""
    if label and label.lower() not in generic_labels:
        return label
    dir_counts: Counter = Counter()
    for m in members:
        parts = m.node_id.split("/")
        # Use the deepest meaningful directory segment
        for p in reversed(parts[:-1]):
            if p.lower() not in generic_labels and p not in ("src",):
                dir_counts[p] += 1
                break
    return dir_counts.most_common(1)[0][0] if dir_counts else f"cluster_{cid}"


def _build_community_summary(all_nodes: list[GraphNode]) -> list[dict[str, Any]]:
    """Top-10 communities by size, skipping generic/unhelpful labels."""
    community_groups: dict[int, list[GraphNode]] = defaultdict(list)
    for n in all_nodes:
        if n.node_type == "file" and n.community_id is not None and n.community_id >= 0:
            community_groups[n.community_id].append(n)

    generic_labels = {"packages", "src", "lib", "core", "app", ""}
    largest = sorted(community_groups.items(), key=lambda x: -len(x[1]))[:10]
    # No cohesion in the payload: a 3-decimal internal clustering metric
    # gives an agent nothing to act on. Label + size carry the map.
    return [
        {
            "id": cid,
            "label": _community_display_label(
                _stored_label(members[0]), members, cid, generic_labels
            ),
            "size": len(members),
        }
        for cid, members in largest
    ]


def _stored_label(node: GraphNode) -> str:
    """The label community detection stored on a member, or "" when unreadable."""
    try:
        return json.loads(node.community_meta_json or "{}").get("label", "")
    except (json.JSONDecodeError, TypeError):
        return ""


async def _build_architecture(session: Any, repository: Any) -> dict[str, Any]:
    """KG architecture layers + tour availability."""
    kg_layers = await _get_kg_layers(session, repository.id)
    kg_tour = await _get_kg_tour_steps(session, repository.id)
    if not kg_layers:
        return {}
    return {
        # Names and sizes only: the layer prose restates the overview essay.
        "layers": [
            {
                "name": layer.name,
                "file_count": len(json.loads(layer.node_ids_json) if layer.node_ids_json else []),
            }
            for layer in kg_layers
        ],
        "tour_available": bool(kg_tour),
        "tour_step_count": len(kg_tour),
    }
