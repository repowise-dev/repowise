"""Change-coupling graph surfacing.

Aggregates the per-file co-change partners we already compute and persist
(``GitMetadata.co_change_partners_json``) into a deduplicated repo-wide edge
list, enriched with each file's module / health score / size. Pure surfacing:
no recompute, no new measurement, no LLM.
"""

from repowise.core.analysis.coupling.graph import (
    CouplingEdge,
    CouplingGraph,
    CouplingNode,
    coupling_graph,
)

__all__ = [
    "CouplingEdge",
    "CouplingGraph",
    "CouplingNode",
    "coupling_graph",
]
