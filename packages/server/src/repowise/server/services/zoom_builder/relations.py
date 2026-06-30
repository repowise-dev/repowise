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

from repowise.server.services.c4_builder.labels import coupling_strength, relation_label

from .models import ZoomNode, ZoomRelation
from .tree import file_id


def _chain(node_id: str, nodes: dict[str, ZoomNode]) -> list[str]:
    """Root-to-node id path."""
    out: list[str] = []
    cur: str | None = node_id
    while cur is not None:
        out.append(cur)
        nxt = nodes.get(cur)
        if nxt is None:  # defensive: never happens from the builder
            break
        cur = nxt.parent_id
    out.reverse()
    return out


def aggregate_relations(
    nodes: dict[str, ZoomNode],
    edges: list[tuple[str, str, str]],
) -> tuple[ZoomRelation, ...]:
    """Roll file->file edges up to ``(parent, childA, childB)`` sibling edges."""
    chains: dict[str, list[str]] = {}

    def chain_for(path: str) -> list[str] | None:
        fid = file_id(path)
        if fid not in nodes:
            return None
        cached = chains.get(fid)
        if cached is None:
            cached = _chain(fid, nodes)
            chains[fid] = cached
        return cached

    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    types: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    for src, tgt, etype in edges:
        if src == tgt:
            continue
        ca = chain_for(src)
        cb = chain_for(tgt)
        if ca is None or cb is None:
            continue
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
            continue
        parent = ca[lca_idx]
        child_a = ca[lca_idx + 1]
        child_b = cb[lca_idx + 1]
        if child_a == child_b:
            continue
        key = (parent, child_a, child_b)
        counts[key] += 1
        types[key].add(etype or "imports")

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
