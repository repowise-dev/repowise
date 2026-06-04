"""Architectural layer inference — the grouping spine for the wiki.

Two responsibilities, both pure and deterministic:

1. :func:`infer_layer` — assign every file to exactly one architectural
   layer from its path, using a directory→layer hint table. This is the
   *fallback* used when the knowledge graph has no layer for a file, so the
   wiki can guarantee that **every** ``file_page`` carries a
   ``metadata.layer_name``.

2. :func:`compute_layer_order` — order the layers top→bottom by inter-layer
   **dependency direction** (a layer that imports others sits above the layers
   it imports). This turns the Architecture section from a flat list into a
   hierarchy that teaches how the system is stacked. We reuse the import graph
   already built during ingestion rather than re-deriving fan-in/fan-out.

Neither function does any I/O or depends on graph libraries — they take plain
strings and edge tuples, which keeps them trivially unit-testable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath

# ---------------------------------------------------------------------------
# Directory → layer hint table. Each canonical layer maps to the
# directory-name tokens that imply it. A
# file is assigned the layer of the first matching path segment, scanning
# from the deepest segment outward (the closest directory wins).
# ---------------------------------------------------------------------------

_LAYER_HINTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("CLI", frozenset({"cli", "commands", "cmd", "cli_commands"})),
    ("API", frozenset({"routes", "api", "controllers", "endpoints", "handlers", "routers"})),
    ("Service", frozenset({"services", "core", "lib", "domain", "logic", "usecases"})),
    ("Data", frozenset({"models", "db", "data", "persistence", "repository", "repositories", "store", "stores", "entities"})),
    ("UI", frozenset({"components", "views", "pages", "ui", "layouts", "widgets", "screens"})),
    ("Middleware", frozenset({"middleware", "plugins", "interceptors", "guards"})),
    ("Utility", frozenset({"utils", "helpers", "common", "shared", "tools", "util"})),
    ("Config", frozenset({"config", "constants", "env", "settings", "conf"})),
    ("Test", frozenset({"__tests__", "test", "tests", "spec", "specs", "e2e"})),
    ("Types", frozenset({"types", "interfaces", "schemas", "contracts", "dtos", "typings"})),
)

# Fallback layer for files whose path matches no hint (root scripts, etc.).
DEFAULT_LAYER = "Application"

# Canonical top→bottom dependency rank. Used to seed the ordering and to
# break ties when the import graph is too sparse to imply a direction. Lower
# index = closer to the top (consumers); higher = closer to the bottom
# (foundational): top imports middle imports bottom.
_CANONICAL_RANK: dict[str, int] = {
    "UI": 0,
    "CLI": 1,
    "API": 2,
    "Middleware": 3,
    "Service": 4,
    DEFAULT_LAYER: 5,
    "Data": 6,
    "Types": 7,
    "Config": 8,
    "Utility": 9,
    "Test": 10,
}


def infer_layer(path: str) -> str:
    """Return the architectural layer name for *path*.

    Scans path segments from the deepest directory outward and returns the
    first layer whose hint set contains a segment. Falls back to
    :data:`DEFAULT_LAYER` when nothing matches.
    """
    segments = [s.lower() for s in PurePosixPath(path).parts[:-1]]  # drop filename
    # Deepest directory first — the closest folder describes the file best.
    for seg in reversed(segments):
        for layer_name, tokens in _LAYER_HINTS:
            if seg in tokens:
                return layer_name
    return DEFAULT_LAYER


def compute_layer_order(
    file_layers: Mapping[str, str],
    import_edges: Iterable[tuple[str, str]],
) -> list[str]:
    """Order the layers present in *file_layers* top→bottom by dependency direction.

    Parameters
    ----------
    file_layers:
        ``{file_path: layer_name}`` for every documented file.
    import_edges:
        ``(src, dst)`` pairs meaning *src imports dst* (file paths). External
        nodes (``external:*``) and intra-layer edges are ignored.

    A layer that does more importing than being-imported sits higher (it
    consumes the layers below it). We rank by ``in - out`` ascending: a layer
    imported by many but importing few is foundational (bottom); a layer that
    imports many but is imported by few is a consumer (top). Ties fall back to
    the canonical rank so the result is stable on graphs with no clear
    direction.
    """
    layers = sorted(set(file_layers.values()))
    if len(layers) <= 1:
        return layers

    out_deg: dict[str, int] = defaultdict(int)  # edges leaving the layer
    in_deg: dict[str, int] = defaultdict(int)  # edges entering the layer
    for src, dst in import_edges:
        if src.startswith("external:") or dst.startswith("external:"):
            continue
        ls = file_layers.get(src)
        ld = file_layers.get(dst)
        if not ls or not ld or ls == ld:
            continue
        out_deg[ls] += 1
        in_deg[ld] += 1

    def sort_key(layer: str) -> tuple[int, int]:
        # Net "imported-ness": more incoming than outgoing → foundational →
        # sorts later (bottom). Negate out so consumers float to the top.
        net = in_deg[layer] - out_deg[layer]
        return (net, _CANONICAL_RANK.get(layer, len(_CANONICAL_RANK)))

    return sorted(layers, key=sort_key)
