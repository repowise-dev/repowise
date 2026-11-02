"""Aggregate file->file edges into parent-relative sibling relations.

The canvas draws relations between the children of whatever node you are zoomed
into. So an edge between two files is attributed to the *lowest common ancestor*
(LCA) of the two file leaves: under that parent, it becomes an edge between the
two children whose subtrees the files live in. Counts are summed per directed
sibling pair and labelled / coupling-tiered with the same helpers the C4 view
uses, so the vocabulary stays consistent.

Pure: operates on the in-memory tree + edge list, no DB.
"""

from __future__ import annotations

from collections import defaultdict

from repowise.core.analysis.c4.labels import coupling_strength, relation_label

from .models import ZoomNode, ZoomRelation, ancestry
from .tree import file_id


def _sibling_key(ca: list[str] | None, cb: list[str] | None) -> tuple[str, str, str] | None:
    """``(lowest common ancestor, child toward a, child toward b)`` for two chains.

    None when either file is off the tree or the two never split into distinct
    siblings.
    """
    if ca is None or cb is None:
        return None
    # Lowest common ancestor = last shared prefix index.
    lca_idx = -1
    for i in range(min(len(ca), len(cb))):
        if ca[i] == cb[i]:
            lca_idx = i
        else:
            break
    # The child of the LCA on each path (the LCA itself is shared, so both
    # files have a next node below it that differs).
    if lca_idx < 0 or lca_idx + 1 >= len(ca) or lca_idx + 1 >= len(cb):
        return None
    child_a = ca[lca_idx + 1]
    child_b = cb[lca_idx + 1]
    if child_a == child_b:
        return None
    return ca[lca_idx], child_a, child_b


def aggregate_relations(
    nodes: dict[str, ZoomNode],
    edges: list[tuple[str, str, str]],
) -> tuple[ZoomRelation, ...]:
    """Roll file->file edges up to ``(parent, childA, childB)`` sibling edges."""
    chains: dict[str, list[str] | None] = {}

    def chain_for(path: str) -> list[str] | None:
        if path not in chains:
            fid = file_id(path)
            chains[path] = ancestry(fid, nodes) if fid in nodes else None
        return chains[path]

    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    types: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    # A file pair recurs once per import, call and co-change between the two
    # files (130k edges, 73k pairs on a 7k-file repository), so its key is
    # worked out once. Nested by source rather than keyed by a pair tuple, so
    # the memo adds no object per pair for the garbage collector to walk.
    pair_keys: dict[str, dict[str, tuple[str, str, str] | None]] = defaultdict(dict)

    for src, tgt, etype in edges:
        if src == tgt:
            continue
        by_target = pair_keys[src]
        if tgt in by_target:
            key = by_target[tgt]
        else:
            key = by_target[tgt] = _sibling_key(chain_for(src), chain_for(tgt))
        if key is None:
            continue
        counts[key] += 1
        types[key].add(etype)

    relations: list[ZoomRelation] = []
    for (parent, child_a, child_b), count in counts.items():
        etypes = tuple(sorted(types[(parent, child_a, child_b)]))
        relations.append(
            ZoomRelation(
                parent_id=parent,
                source_id=child_a,
                target_id=child_b,
                label=relation_label(etypes),
                edge_count=count,
                coupling=coupling_strength(count),
            )
        )
    relations.sort(key=lambda r: (r.parent_id, -r.edge_count, r.source_id, r.target_id))
    return tuple(relations)
