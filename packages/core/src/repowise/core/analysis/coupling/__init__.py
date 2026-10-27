"""Change-coupling graph surfacing.

Aggregates the per-file co-change partners we already compute and persist
(``GitMetadata.co_change_partners_json``) into a deduplicated repo-wide edge
list, enriched with each file's module / health score / size, then scopes it
to areas (:mod:`.scope`) and folds it into area pairs (:mod:`.areas`). Pure
surfacing: no recompute, no new measurement, no LLM.
"""

from repowise.core.analysis.coupling.areas import (
    AREA_PAIR_LIMIT,
    COUPLED_AREA_LIMIT,
    TOP_PAIRS,
    area_coupling,
    coupled_areas,
    linked_area_pairs,
)
from repowise.core.analysis.coupling.graph import (
    CouplingEdge,
    CouplingGraph,
    CouplingNode,
    coupling_edges,
    coupling_graph,
    graph_from_edges,
)
from repowise.core.analysis.coupling.scope import (
    MAX_DEPTH,
    CouplingScope,
    PlacedEdge,
    area_placer,
    place_edges,
)

__all__ = [
    "AREA_PAIR_LIMIT",
    "COUPLED_AREA_LIMIT",
    "MAX_DEPTH",
    "TOP_PAIRS",
    "CouplingEdge",
    "CouplingGraph",
    "CouplingNode",
    "CouplingScope",
    "PlacedEdge",
    "area_coupling",
    "area_placer",
    "coupled_areas",
    "coupling_edges",
    "coupling_graph",
    "graph_from_edges",
    "linked_area_pairs",
    "place_edges",
]
