"""Deterministic squarified-treemap layout for the zoom map.

Each parent's children are packed into the parent's unit ``[0,1]`` rectangle,
with area proportional to ``importance`` (so the execution-relevant nodes the
scorer surfaced are also the biggest boxes). Rectangles are emitted in parent
space; the renderer maps a child straight to screen by scaling content by
``(w, h)`` and translating by ``(x, y)`` (clip-and-scale recursion).

Squarified treemap (Bruls, Huizing, van Wijk) keeps boxes close to square so
labels stay readable. The squarify pass itself is iterative so a single flat
folder with thousands of files cannot blow the recursion limit; the tree walk
(``place_children``) recurses one frame per *level*, which the single-child
chain compression in ``tree.py`` keeps shallow. Pure, deterministic, no DB.
"""

from __future__ import annotations

from dataclasses import replace

from .models import ZoomNode, ZoomRect

_EPS = 1e-9


def _worst(row: list[float], length: float) -> float:
    """Worst aspect ratio of a row of areas laid along a side of ``length``."""
    if length <= 0:
        return float("inf")
    total = sum(row)
    if total <= 0:
        return float("inf")
    side = total / length
    worst = 0.0
    for area in row:
        other = area / side if side > 0 else 0.0
        if other <= 0:
            return float("inf")
        ratio = max(side / other, other / side)
        worst = max(worst, ratio)
    return worst


def _emit_row(
    row: list[float], x: float, y: float, dx: float, dy: float
) -> tuple[list[tuple[float, float, float, float]], tuple[float, float, float, float]]:
    """Place ``row`` along the shorter side; return its rects + the leftover."""
    total = sum(row)
    rects: list[tuple[float, float, float, float]] = []
    if dx >= dy:  # lay the row as a vertical column of fixed width
        width = total / dy if dy > 0 else 0.0
        yy = y
        for area in row:
            h = area / width if width > 0 else 0.0
            rects.append((x, yy, width, h))
            yy += h
        return rects, (x + width, y, dx - width, dy)
    # lay the row as a horizontal strip of fixed height
    height = total / dx if dx > 0 else 0.0
    xx = x
    for area in row:
        w = area / height if height > 0 else 0.0
        rects.append((xx, y, w, height))
        xx += w
    return rects, (x, y + height, dx, dy - height)


def _squarify(areas: list[float], x: float, y: float, dx: float, dy: float) -> list[tuple]:
    """Lay out ``areas`` (summing to ``dx*dy``, sorted desc) into rectangles."""
    rects: list[tuple] = []
    remaining = list(areas)
    while remaining:
        if dx <= 0 or dy <= 0:
            # Degenerate strip: stack the rest with zero area (keeps positions).
            for _ in remaining:
                rects.append((x, y, 0.0, 0.0))
            break
        shorter = min(dx, dy)
        row: list[float] = [remaining[0]]
        i = 1
        while i < len(remaining):
            if _worst(row, shorter) < _worst([*row, remaining[i]], shorter):
                break
            row.append(remaining[i])
            i += 1
        placed, (x, y, dx, dy) = _emit_row(row, x, y, dx, dy)
        rects.extend(placed)
        remaining = remaining[i:]
    return rects


def lay_out(root_id: str, nodes: dict[str, ZoomNode]) -> dict[str, ZoomNode]:
    """Return ``nodes`` with a ``layout`` rect on every node (parent space)."""
    updated: dict[str, ZoomNode] = dict(nodes)
    updated[root_id] = replace(updated[root_id], layout=ZoomRect(0.0, 0.0, 1.0, 1.0))

    def place_children(node_id: str) -> None:
        node = updated[node_id]
        children = list(node.children)
        if children:
            ordered = sorted(children, key=lambda c: (-updated[c].importance, updated[c].name))
            weights = [updated[c].importance + _EPS for c in ordered]
            total = sum(weights)
            areas = [w / total for w in weights]  # sum to 1 == unit-square area
            rects = _squarify(areas, 0.0, 0.0, 1.0, 1.0)
            for cid, (rx, ry, rw, rh) in zip(ordered, rects, strict=True):
                updated[cid] = replace(updated[cid], layout=ZoomRect(rx, ry, rw, rh))
            for cid in children:
                place_children(cid)

    place_children(root_id)
    return updated
