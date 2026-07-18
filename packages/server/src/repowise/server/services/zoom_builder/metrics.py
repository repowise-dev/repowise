"""Roll per-file flags up the containment tree into per-node counts.

A file leaf contributes its own flags; every ancestor sums its subtree. The
renderer shows these as chips (``12 files``, ``3 hotspots``) at any zoom depth
without walking the tree itself. Pure post-order aggregation, no DB.
"""

from __future__ import annotations

from dataclasses import replace

from .models import ZoomMetrics, ZoomNode


def rollup_metrics(root_id: str, nodes: dict[str, ZoomNode]) -> dict[str, ZoomNode]:
    """Return ``nodes`` with ``metrics`` filled on every node.

    Recurses one frame per tree *level*; single-child folder-chain compression
    (``tree.py``) keeps the tree shallow (branching levels only), so this stays
    far below the recursion limit even on deeply-nested package layouts.
    """
    updated: dict[str, ZoomNode] = dict(nodes)

    def visit(node_id: str) -> ZoomMetrics:
        node = updated[node_id]
        if node.kind == "file":
            m = ZoomMetrics(
                file_count=1,
                descendant_count=0,
                hotspot_count=1 if node.is_hotspot else 0,
                dead_count=1 if node.is_dead else 0,
                entry_point_count=1 if node.is_entry_point else 0,
                on_flow_count=1 if node.on_flow else 0,
            )
            updated[node_id] = replace(node, metrics=m)
            return m

        files = hot = dead = entry = flow = descendants = 0
        for cid in node.children:
            cm = visit(cid)
            files += cm.file_count
            hot += cm.hotspot_count
            dead += cm.dead_count
            entry += cm.entry_point_count
            flow += cm.on_flow_count
            descendants += cm.descendant_count + 1  # the child itself + its subtree
        m = ZoomMetrics(
            file_count=files,
            descendant_count=descendants,
            hotspot_count=hot,
            dead_count=dead,
            entry_point_count=entry,
            on_flow_count=flow,
        )
        updated[node_id] = replace(node, metrics=m)
        return m

    visit(root_id)
    return updated


def rollup_health(root_id: str, nodes: dict[str, ZoomNode]) -> dict[str, ZoomNode]:
    """Fill ``health_score`` on every node from the file leaves.

    A file keeps its own score (already set on the leaf). Each container gets the
    loc-weighted mean of its scored descendants, matching how the /files treemap
    colors a folder tile, so the two surfaces stay pixel-consistent. Health is
    sparse (files can be unscored or excluded); those leaves carry weight 0 and a
    container with no scored descendant stays ``None``. Post-order, no DB.
    """
    updated: dict[str, ZoomNode] = dict(nodes)

    def visit(node_id: str) -> tuple[float, float]:
        """Return ``(weighted_sum, weight)`` over this subtree's scored files."""
        node = updated[node_id]
        if node.kind == "file":
            if node.health_score is None:
                return 0.0, 0.0
            w = float(max(node.loc, 1))
            return node.health_score * w, w

        weighted_sum = 0.0
        weight = 0.0
        for cid in node.children:
            cs, cw = visit(cid)
            weighted_sum += cs
            weight += cw
        score = weighted_sum / weight if weight > 0 else None
        updated[node_id] = replace(node, health_score=score)
        return weighted_sum, weight

    visit(root_id)
    return updated
