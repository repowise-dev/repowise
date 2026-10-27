"""One symbol's relations, each kind counted and capped on its own.

Shared by `/api/symbols/detail` (the routed symbol page) and
`/api/graph/{repo}/callers-callees` (the symbol drawer). It lived in
`routers/symbols.py` while only the first surface used it; the drawer then had
honest call counts but no heritage, so the two surfaces disagreed about what
reaches a symbol. Grouping in the client would have been a second copy of the
vocabulary, which is the failure mode this whole area keeps re-creating.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ingestion.models import SYMBOL_USE_EDGE_TYPES
from repowise.core.persistence import crud
from repowise.server.schemas.intelligence import (
    SYMBOL_RELATION_GROUP_OF,
    CallerCalleeEntry,
    SymbolRelationGroup,
)

#: Rows served per relation kind per direction. Calls get the larger cap
#: because they are the reason most readers open the page; the rest exist to
#: be *named and counted*, and a caller who wants all 1,516 subclasses has the
#: graph page for it.
CALL_ROW_CAP = 40
RELATION_ROW_CAP = 10


@dataclass
class SymbolRelations:
    """Callers/callees plus every other relation kind, each counted honestly."""

    callers: list[CallerCalleeEntry] = field(default_factory=list)
    callees: list[CallerCalleeEntry] = field(default_factory=list)
    caller_total: int = 0
    callee_total: int = 0
    groups: list[SymbolRelationGroup] = field(default_factory=list)
    #: Degree across every use edge type, summed from the same counts the
    #: rows were cut from rather than re-queried, so the two cannot disagree.
    in_degree: int = 0
    out_degree: int = 0


async def load_symbol_relations(
    session: AsyncSession,
    repo_id: str,
    symbol_id: str,
    *,
    present: bool,
    call_row_cap: int = CALL_ROW_CAP,
    relation_row_cap: int = RELATION_ROW_CAP,
) -> SymbolRelations:
    """Fetch this symbol's relations, each kind counted and capped on its own.

    One fetch per edge type rather than one for all of
    `SYMBOL_USE_EDGE_TYPES`, because a shared cap ranked by confidence lets the
    commonest kind evict every other. Measured on the live django index:
    `Model` has 8 callers and 1,516 subclasses, and the single 40-row cut
    served 39 subclasses and 1 caller; `TestCase` has 3 callers and 868
    subclasses and served none of its callers at all. Grouping alone does not
    fix it — `extends` would still evict `implements` inside one heritage cap,
    leaving "Implemented by (5)" over an empty list, which is the same lie one
    level down.

    Cost is unchanged for the common symbol. Only edge types the counts show
    are present get fetched, and most symbols have exactly one, so this is the
    same query count as the single-fetch version it replaces.
    """
    out = SymbolRelations()
    if not present:
        return out

    # Every total in two queries, before any rows. They say which edge types
    # exist at all, so nothing is fetched speculatively, and they are the
    # numbers the surface reports.
    totals = await crud.get_node_degree_by_edge_type(
        session, repo_id, symbol_id, edge_types=sorted(SYMBOL_USE_EDGE_TYPES)
    )
    if not totals:
        return out
    out.in_degree = sum(t["in_degree"] for t in totals.values())
    out.out_degree = sum(t["out_degree"] for t in totals.values())

    edges_by_type: dict[str, list] = {}
    for edge_type in sorted(totals):
        edges_by_type[edge_type] = await crud.get_graph_edges_for_node(
            session,
            repo_id,
            symbol_id,
            direction="both",
            edge_types=[edge_type],
            limit=call_row_cap if edge_type == "calls" else relation_row_cap,
        )

    # Hydrated once for every edge type at once: the neighbour lookup is the
    # expensive half and it does not care which relation asked.
    other_ids = {
        e.source_node_id if e.target_node_id == symbol_id else e.target_node_id
        for edges in edges_by_type.values()
        for e in edges
    }
    node_map = await crud.get_graph_nodes_by_ids(session, repo_id, list(other_ids))

    for edge_type, edges in edges_by_type.items():
        inbound: list[CallerCalleeEntry] = []
        outbound: list[CallerCalleeEntry] = []
        # A self-edge — a recursive call — matches both of the direction
        # queries, so it comes back twice. It is also counted once in each
        # direction by the totals, so it belongs on both sides exactly once:
        # dedupe first, then place each edge on every side it touches. Served
        # once, its row is a permanent "+1 more" no paging can reach.
        for e in {edge.id: edge for edge in edges}.values():
            entry = CallerCalleeEntry.from_edge(e, node_id=symbol_id, node_map=node_map)
            if e.target_node_id == symbol_id:
                inbound.append(entry)
            if e.source_node_id == symbol_id:
                outbound.append(entry)
        for rows in (inbound, outbound):
            rows.sort(key=lambda r: (-r.confidence, r.name))

        if edge_type == "calls":
            out.callers = inbound
            out.callees = outbound
            out.caller_total = totals["calls"]["in_degree"]
            out.callee_total = totals["calls"]["out_degree"]
            continue

        # Keyed per edge type, not per group: "Extended by" and "Implemented
        # by" are different sentences and a reader needs to know which they
        # are in.
        for direction, rows in (("in", inbound), ("out", outbound)):
            total = totals[edge_type]["in_degree" if direction == "in" else "out_degree"]
            if not total:
                continue
            out.groups.append(
                SymbolRelationGroup(
                    direction=direction,
                    edge_type=edge_type,
                    group=SYMBOL_RELATION_GROUP_OF[edge_type],
                    total=total,
                    rows=rows,
                )
            )

    out.groups.sort(key=lambda g: (g.direction, -g.total, g.edge_type))
    return out
