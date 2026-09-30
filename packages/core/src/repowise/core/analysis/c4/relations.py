"""Roll file-to-file graph edges up to box-to-box relations.

Each endpoint maps to its box (a container, a component, or an external
system), counts are summed per directed box pair with the edge types seen, and
self-loops are dropped. Co-change edges are history, not a dependency, so they
are left out unless a caller opts in. Edges touching a configuration file are
dropped: reading config is not a dependency between boxes. Edges touching a
test file are dropped too: a test importing a package is not a dependency of
the box the test sits in.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from repowise.core.support_paths import is_config_path
from repowise.core.test_paths import is_test_related_path

#: ``(source box, target box) -> (file-pair count, edge types)``.
BoxEdges = dict[tuple[str, str], tuple[int, frozenset[str]]]


def roll_up_edges(
    edges: Iterable[tuple[str, str, str]],
    file_to_box: Mapping[str, str],
    *,
    file_to_external: Mapping[str, str] | None = None,
    include_co_changes: bool = False,
) -> BoxEdges:
    """Sum ``(source, target, edge_type)`` rows into box pairs.

    *file_to_external* maps ``external:*`` node ids to an external box, so an
    import of a third-party package becomes a box-to-external edge. Only file
    endpoints are keyed, so containment edges (file to symbol) never land.
    """
    externals = file_to_external or {}
    counts: dict[tuple[str, str], int] = defaultdict(int)
    types: dict[tuple[str, str], set[str]] = defaultdict(set)
    for src, tgt, etype in edges:
        if etype == "co_changes" and not include_co_changes:
            continue
        src_box = file_to_box.get(src)
        if src_box is None:
            continue
        tgt_box = file_to_box[tgt] if tgt in file_to_box else externals.get(tgt)
        if tgt_box is None or src_box == tgt_box:
            continue
        if is_config_path(src) or is_config_path(tgt):
            continue
        if is_test_related_path(src) or is_test_related_path(tgt):
            continue
        counts[(src_box, tgt_box)] += 1
        types[(src_box, tgt_box)].add(etype)
    return {key: (count, frozenset(types[key])) for key, count in counts.items()}
