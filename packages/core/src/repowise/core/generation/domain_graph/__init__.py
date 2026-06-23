"""Behavior-oriented domain graph (Domain -> Flow -> Step).

A synthesized artifact layered on top of the structural knowledge graph that
answers "what does this system do?". Built after wiki pages exist, from layers
+ file summaries + the heaviest import edges (not raw source), with a fixed,
bounded LLM budget. See :mod:`.synthesis` for the orchestration and
:mod:`.models` for the in-memory shape.
"""

from __future__ import annotations

from .models import (
    CrossDomainEdge,
    DomainGraph,
    DomainNode,
    FlowNode,
    StepNode,
)
from .persistence_map import flatten_edges, flatten_nodes
from .synthesis import synthesize_domain_graph

__all__ = [
    "CrossDomainEdge",
    "DomainGraph",
    "DomainNode",
    "FlowNode",
    "StepNode",
    "flatten_edges",
    "flatten_nodes",
    "synthesize_domain_graph",
]
