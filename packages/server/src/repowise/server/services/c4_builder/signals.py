"""Roll per-file health signals (hotspots, dead code) up to C4 boxes.

The graph already carries which files are churn hotspots (``git_metadata``)
and which are unreachable dead code (``dead_code_findings``); the container
view simply never counted them, so every box serialized ``0``. This module is
the pure aggregation: given a file->box map and the sets of flagged file
paths, count per box. The DB load lives in the builder; the counting is here
so it unit-tests without a session.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

from .models import BoxSignals


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


def build_box_signals(
    file_to_box: Mapping[str, str],
    *,
    hotspot_paths: Iterable[str] = (),
    dead_paths: Iterable[str] = (),
    file_layers: Mapping[str, str] | None = None,
    file_owners: Mapping[str, str] | None = None,
    file_bus_factors: Mapping[str, int] | None = None,
) -> dict[str, BoxSignals]:
    """Roll every per-file signal up to its box in one pass.

    Pure, so the aggregation rules are testable without a database and the
    caller pays for exactly one read of each source table.

    Ownership is the person owning the most files in the box, with their
    share. Bus factor is the *lowest* among the box's files rather than the
    mean — one file only one person understands is the risk worth surfacing,
    and averaging hides it.
    """
    hotspots = set(hotspot_paths)
    dead = set(dead_paths)
    layers = file_layers or {}
    owners = file_owners or {}
    bus_factors = file_bus_factors or {}

    hot_by_box: dict[str, int] = defaultdict(int)
    dead_by_box: dict[str, int] = defaultdict(int)
    layers_by_box: dict[str, set[str]] = defaultdict(set)
    owners_by_box: dict[str, Counter[str]] = defaultdict(Counter)
    owned_files_by_box: dict[str, int] = defaultdict(int)
    bus_by_box: dict[str, int] = {}
    boxes: set[str] = set()

    for path, box in file_to_box.items():
        boxes.add(box)
        if path in hotspots:
            hot_by_box[box] += 1
        if path in dead:
            dead_by_box[box] += 1
        layer = layers.get(path)
        if layer:
            layers_by_box[box].add(layer)
        owner = owners.get(path)
        if owner:
            owners_by_box[box][owner] += 1
            owned_files_by_box[box] += 1
        bus = bus_factors.get(path)
        if bus is not None:
            current = bus_by_box.get(box)
            bus_by_box[box] = bus if current is None else min(current, bus)

    out: dict[str, BoxSignals] = {}
    for box in boxes:
        owner_name: str | None = None
        owner_pct: float | None = None
        counts = owners_by_box.get(box)
        if counts:
            # Ties broken by name so a re-export of an unchanged repo produces
            # an unchanged file.
            owner_name, owned = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
            total = owned_files_by_box[box]
            owner_pct = round(100.0 * owned / total, 1) if total else None
        out[box] = BoxSignals(
            hotspot_count=hot_by_box[box],
            dead_count=dead_by_box[box],
            layers=tuple(sorted(layers_by_box.get(box, ()))),
            primary_owner=owner_name,
            primary_owner_pct=owner_pct,
            min_bus_factor=bus_by_box.get(box),
        )
    return out
