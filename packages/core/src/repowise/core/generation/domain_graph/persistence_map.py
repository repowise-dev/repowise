"""(d) Persistence mapping: flatten a :class:`DomainGraph` into storable rows.

Pure transform from the in-memory graph to the row dicts the CRUD layer writes
(``domain_graph_nodes`` / ``domain_graph_edges``), with the renderable page
content attached to domain and flow rows. Testable with fixture graphs - no DB.

Node row shape::

    {node_id, kind, name, summary, parent_id, step_order,
     implements (list[str]), page_title, page_content, display_order}

Edge row shape::

    {source_node_id, target_node_id, edge_type, weight}
"""

from __future__ import annotations

from .models import (
    EDGE_CONTAINS_FLOW,
    EDGE_CROSS_DOMAIN,
    EDGE_FLOW_STEP,
    DomainGraph,
)
from .render import render_domain_page, render_flow_page


def flatten_nodes(
    graph: DomainGraph,
    hotspot_node_ids: set[str] | None = None,
) -> list[dict]:
    """Domain, flow, and step rows in stable, display-ordered sequence."""
    rows: list[dict] = []
    for d_order, domain in enumerate(graph.domains):
        title, _summary, content = render_domain_page(domain)
        rows.append(
            {
                "node_id": domain.id,
                "kind": "domain",
                "name": domain.name,
                "summary": domain.summary,
                "parent_id": None,
                "step_order": None,
                "implements": list(domain.member_node_ids),
                "page_title": title,
                "page_content": content,
                "display_order": d_order,
            }
        )
        for f_order, flow in enumerate(domain.flows):
            flow_id = flow.id(domain.id)
            f_title, _f_summary, f_content = render_flow_page(domain, flow, hotspot_node_ids)
            rows.append(
                {
                    "node_id": flow_id,
                    "kind": "flow",
                    "name": flow.name,
                    "summary": flow.summary,
                    "parent_id": domain.id,
                    "step_order": None,
                    "implements": [],
                    "page_title": f_title,
                    "page_content": f_content,
                    "display_order": f_order,
                }
            )
            for step in flow.steps:
                rows.append(
                    {
                        "node_id": step.id(flow_id),
                        "kind": "step",
                        "name": step.name,
                        "summary": step.summary,
                        "parent_id": flow_id,
                        "step_order": step.order,
                        "implements": list(step.implements),
                        "page_title": "",
                        "page_content": "",
                        "display_order": step.order,
                    }
                )
    return rows


def flatten_edges(graph: DomainGraph) -> list[dict]:
    """contains_flow (domain->flow), flow_step (flow->step, weight=order),
    and cross_domain (domain->domain, weight=import count) edges."""
    rows: list[dict] = []
    for domain in graph.domains:
        for flow in domain.flows:
            flow_id = flow.id(domain.id)
            rows.append(
                {
                    "source_node_id": domain.id,
                    "target_node_id": flow_id,
                    "edge_type": EDGE_CONTAINS_FLOW,
                    "weight": 0.0,
                }
            )
            for step in flow.steps:
                rows.append(
                    {
                        "source_node_id": flow_id,
                        "target_node_id": step.id(flow_id),
                        "edge_type": EDGE_FLOW_STEP,
                        "weight": float(step.order),
                    }
                )
    for edge in graph.cross_domain:
        rows.append(
            {
                "source_node_id": edge.source,
                "target_node_id": edge.target,
                "edge_type": EDGE_CROSS_DOMAIN,
                "weight": float(edge.weight),
            }
        )
    return rows
