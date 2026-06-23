"""Roll per-file health signals (hotspots, dead code) up to C4 boxes.

The graph already carries which files are churn hotspots (``git_metadata``)
and which are unreachable dead code (``dead_code_findings``); the container
view simply never counted them, so every box serialized ``0``. This module is
the pure aggregation: given a file->box map and the sets of flagged file
paths, count per box. The DB load lives in the builder; the counting is here
so it unit-tests without a session.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping


def count_box_signals(
    file_to_box: Mapping[str, str],
    hotspot_paths: Iterable[str],
    dead_paths: Iterable[str],
) -> dict[str, tuple[int, int]]:
    """Return ``box_id -> (hotspot_count, dead_count)``.

    Every box present in ``file_to_box`` appears in the result (with zeros
    when it owns no flagged files), so callers can annotate unconditionally.
    """
    hotspots = set(hotspot_paths)
    dead = set(dead_paths)
    hot_by_box: dict[str, int] = defaultdict(int)
    dead_by_box: dict[str, int] = defaultdict(int)
    boxes: set[str] = set()
    for path, box in file_to_box.items():
        boxes.add(box)
        if path in hotspots:
            hot_by_box[box] += 1
        if path in dead:
            dead_by_box[box] += 1
    return {box: (hot_by_box[box], dead_by_box[box]) for box in boxes}
