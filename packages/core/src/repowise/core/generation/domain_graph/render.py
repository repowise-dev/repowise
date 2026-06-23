"""Renderable wiki page content for domains and flows (pure).

Produces ``(title, summary, markdown)`` triples so the domain graph can be
persisted as embeddable page content alongside its structural rows. No DB, no
LLM - Phase 5 decides how to surface these; here we only render.
"""

from __future__ import annotations

from .models import DomainNode, FlowNode


def _file_link(node_id: str) -> str:
    path = node_id.removeprefix("file:")
    return f"`{path}`"


def render_domain_page(domain: DomainNode) -> tuple[str, str, str]:
    """A domain overview page: summary + its flows with their step counts."""
    title = domain.name
    summary = domain.summary or f"The {domain.name} capability."
    lines = [f"# {title}", "", summary, ""]
    if domain.flows:
        lines.append("## Flows")
        lines.append("")
        for flow in domain.flows:
            step_word = "step" if len(flow.steps) == 1 else "steps"
            lines.append(f"- **{flow.name}** ({len(flow.steps)} {step_word})")
            if flow.summary:
                lines.append(f"  - {flow.summary}")
    return title, summary, "\n".join(lines).rstrip() + "\n"


def render_flow_page(
    domain: DomainNode,
    flow: FlowNode,
    hotspot_node_ids: set[str] | None = None,
) -> tuple[str, str, str]:
    """A flow page: ordered steps with the files each step implements.

    When *hotspot_node_ids* is supplied, a step that touches a churn hotspot is
    annotated so the riskiest stage of the flow is visible at a glance.
    """
    hotspots = hotspot_node_ids or set()
    title = f"{flow.name} ({domain.name})"
    summary = flow.summary or f"How {domain.name.lower()} performs {flow.name.lower()}."
    lines = [f"# {flow.name}", "", f"_Part of the {domain.name} domain._", "", summary, ""]
    lines.append("## Steps")
    lines.append("")
    for step in flow.steps:
        risky = any(nid in hotspots for nid in step.implements)
        marker = " ⚠ hotspot" if risky else ""
        lines.append(f"{step.order}. **{step.name}**{marker}")
        if step.summary:
            lines.append(f"   - {step.summary}")
        for nid in step.implements:
            lines.append(f"   - {_file_link(nid)}")
    return title, summary, "\n".join(lines).rstrip() + "\n"
