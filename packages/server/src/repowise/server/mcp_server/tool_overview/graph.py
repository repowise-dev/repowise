"""Graph-derived overview blocks: code communities, layers and package dependencies."""

from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.generation.layers import is_adjacent_layer
from repowise.core.persistence.crud import (
    get_kg_layers as _get_kg_layers,
)
from repowise.core.persistence.crud import (
    get_kg_tour_steps as _get_kg_tour_steps,
)
from repowise.core.persistence.models import GraphNode
from repowise.server.mcp_server._helpers import filter_graph_nodes
from repowise.server.mcp_server._index_state import index_state_key
from repowise.server.services.c4_builder import container_dependencies

#: Heaviest package edges the overview carries; the container view draws them all.
_DEPENDENCY_EDGES = 10

# The roll-up reads every graph edge, and it changes only with the index, so it
# is cached per repo, index state and exclusion rules (the compiled rule set is
# itself cached, so its identity is stable while the rules are).
_DEPENDENCY_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}
_DEPENDENCY_CACHE_LIMIT = 16


def reset_cache() -> None:
    """Drop every cached dependency block. For tests and an in-process re-index."""
    _DEPENDENCY_CACHE.clear()


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


async def _build_architecture(
    session: Any, repository: Any, package_roots: list[str]
) -> dict[str, Any]:
    """KG architecture layers, in stack order, + tour availability."""
    kg_layers = [
        layer
        for layer in await _get_kg_layers(session, repository.id)
        if not is_adjacent_layer(layer.layer_id)
    ]
    kg_tour = await _get_kg_tour_steps(session, repository.id)
    if not kg_layers:
        return {}
    return {
        # Names, sizes and roots only: the layer prose restates the overview essay.
        "layers": [_layer_row(layer, package_roots) for layer in kg_layers],
        "tour_available": bool(kg_tour),
        "tour_step_count": len(kg_tour),
    }


def _layer_row(layer: Any, package_roots: list[str]) -> dict[str, Any]:
    ids = json.loads(layer.node_ids_json) if layer.node_ids_json else []
    row: dict[str, Any] = {"name": layer.name, "file_count": len(ids)}
    root = _majority_root([i.removeprefix("file:") for i in ids])
    # A directory above two packages (``packages/``) locates nothing.
    if root and sum(p.startswith(root + "/") for p in package_roots) < 2:
        row["root"] = root
    return row


def _majority_root(paths: list[str]) -> str | None:
    """The deepest directory holding most of *paths*, or ``None``."""
    under: Counter = Counter()
    for path in paths:
        parts = path.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            under["/".join(parts[:depth])] += 1
    held = [d for d, n in under.items() if 2 * n > len(paths)]
    return max(held, key=lambda d: (d.count("/"), under[d]), default=None)


async def _build_package_dependencies(
    session: Any, repository: Any, exclude_spec: Any = None
) -> dict[str, Any]:
    """Package-to-package edges, heaviest first; empty for a single-package repo.

    The containers and roll-up are the container view's, so test files add no
    weight in either. ``weight`` counts the file pairs behind an edge.
    """
    cached = await _container_view(session, repository, exclude_spec)
    # A copy: the response is shaped after this returns.
    return copy.deepcopy(cached["dependencies"])


async def _package_roots(session: Any, repository: Any, exclude_spec: Any = None) -> list[str]:
    """The container view's package roots, the repository root as ``""``."""
    return list((await _container_view(session, repository, exclude_spec))["roots"])


async def _container_view(session: Any, repository: Any, exclude_spec: Any) -> dict[str, Any]:
    key = (repository.id, index_state_key(repository), id(exclude_spec))
    cached = _DEPENDENCY_CACHE.get(key)
    if cached is None:
        cached = await _package_dependencies(session, repository.id, exclude_spec)
        if len(_DEPENDENCY_CACHE) >= _DEPENDENCY_CACHE_LIMIT:
            _DEPENDENCY_CACHE.clear()
        _DEPENDENCY_CACHE[key] = cached
    return cached


async def _package_dependencies(session: Any, repo_id: str, exclude_spec: Any) -> dict[str, Any]:
    containers, relations = await container_dependencies(
        session, repo_id, exclude_spec=exclude_spec
    )
    path_of = {c.id: c.path or "." for c in containers}
    edges = [
        {
            "from": path_of[r.source_id],
            "to": path_of[r.target_id],
            "verb": r.label,
            "weight": r.edge_count,
        }
        for r in relations[:_DEPENDENCY_EDGES]
        if r.source_id in path_of and r.target_id in path_of
    ]
    block: dict[str, Any] = {"edges": edges} if edges else {}
    if edges and len(relations) > len(edges):
        block["edges_total"] = len(relations)
    return {"dependencies": block, "roots": sorted({c.path or "" for c in containers})}


#: Packages whose commands the overview names beside the root's.
_COMMAND_PACKAGES = 8


def _build_commands(repo_root: Any, package_roots: list[str]) -> dict[str, dict[str, str]]:
    """Build, test, lint, dev, format and typecheck commands read from manifests: the root's, then each
    package's that names a task the root does not. Run those from the package."""
    if not repo_root:
        return {}
    # Imported here: the editor-files package is heavy and only this reads it.
    from repowise.core.generation.editor_files.tech_stack import detect_build_commands

    base = Path(repo_root)
    root = detect_build_commands(base)
    out: dict[str, dict[str, str]] = {".": root} if root else {}
    for package in package_roots:
        if len(out) - ("." in out) >= _COMMAND_PACKAGES or not package:
            continue
        found = detect_build_commands(base / package)
        if set(found) - set(root):
            out[package] = found
    return out
