"""(c) Response parsing + node-id resolution/validation.

Pure functions that turn raw LLM JSON into validated :mod:`.models` objects.
The load-bearing guarantee here is **no hallucinated membership**: a domain may
only claim layer ids that were offered, and a step may only implement member
file paths that exist. Unknown ids are dropped (not trusted), empty steps and
flows are pruned, and step orders are renumbered contiguously. Everything is
testable with fixture strings - no LLM, no DB.
"""

from __future__ import annotations

import json

import structlog

from .models import DomainNode, FlowNode, StepNode

logger = structlog.get_logger(__name__)


def parse_json(content: str) -> dict | None:
    """Extract a JSON object from an LLM response, tolerating code fences."""
    content = (content or "").strip()
    if content.startswith("```"):
        content = "\n".join(
            line for line in content.split("\n") if not line.strip().startswith("```")
        )
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end])
            except json.JSONDecodeError:
                return None
    return None


def _slugify(value: str, fallback: str) -> str:
    cleaned = "".join(c if c.isalnum() else "-" for c in value.lower())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or fallback


def parse_domains(content: str, valid_layer_ids: set[str]) -> list[DomainNode]:
    """Parse the domain-naming response into domains with validated membership.

    A layer is assigned to the first domain that claims it (one domain per
    cluster). Domains left with no valid layer are dropped - a domain must be
    grounded in a real cluster. ``member_node_ids`` is filled in later by the
    caller once the layer->file mapping is known.
    """
    parsed = parse_json(content)
    if not parsed or not isinstance(parsed.get("domains"), list):
        return []

    domains: list[DomainNode] = []
    claimed: set[str] = set()
    seen_slugs: set[str] = set()
    rejected = 0
    for i, item in enumerate(parsed["domains"]):
        if not isinstance(item, dict):
            continue
        members: list[str] = []
        for lid in item.get("member_layer_ids", []):
            if lid in valid_layer_ids and lid not in claimed:
                members.append(lid)
                claimed.add(lid)
            elif lid not in valid_layer_ids:
                rejected += 1
        if not members:
            continue
        name = str(item.get("name", "")).strip() or f"Domain {i + 1}"
        slug = _slugify(str(item.get("slug", "")) or name, f"domain-{i + 1}")
        if slug in seen_slugs:
            slug = f"{slug}-{i + 1}"
        seen_slugs.add(slug)
        domains.append(
            DomainNode(
                slug=slug,
                name=name,
                summary=str(item.get("summary", "")).strip(),
                member_layer_ids=members,
            )
        )
    if rejected:
        logger.warning("domain_graph_rejected_unknown_layers", count=rejected)
    return domains


def resolve_flows(content: str, member_node_ids: set[str]) -> list[FlowNode]:
    """Parse the flow response, keeping only steps that implement real members.

    *member_node_ids* are the domain's file node ids ("file:<path>"). Each
    step's ``implements`` paths are normalized to that form and filtered to the
    member set; a step with no surviving member is dropped, a flow with no
    surviving step is dropped, and step orders are renumbered 1..N.
    """
    parsed = parse_json(content)
    if not parsed or not isinstance(parsed.get("flows"), list):
        return []

    flows: list[FlowNode] = []
    seen_slugs: set[str] = set()
    rejected = 0
    for i, raw_flow in enumerate(parsed["flows"]):
        if not isinstance(raw_flow, dict):
            continue
        steps: list[StepNode] = []
        for raw_step in raw_flow.get("steps", []):
            if not isinstance(raw_step, dict):
                continue
            implements: list[str] = []
            for ref in raw_step.get("implements", []):
                nid = ref if str(ref).startswith("file:") else f"file:{ref}"
                if nid in member_node_ids:
                    if nid not in implements:
                        implements.append(nid)
                else:
                    rejected += 1
            if not implements:
                continue
            steps.append(
                StepNode(
                    order=len(steps) + 1,
                    name=str(raw_step.get("name", "")).strip() or f"Step {len(steps) + 1}",
                    summary=str(raw_step.get("summary", "")).strip(),
                    implements=implements,
                )
            )
        if not steps:
            continue
        name = str(raw_flow.get("name", "")).strip() or f"Flow {i + 1}"
        slug = _slugify(str(raw_flow.get("slug", "")) or name, f"flow-{i + 1}")
        if slug in seen_slugs:
            slug = f"{slug}-{i + 1}"
        seen_slugs.add(slug)
        flows.append(
            FlowNode(
                slug=slug,
                name=name,
                summary=str(raw_flow.get("summary", "")).strip(),
                steps=steps,
            )
        )
    if rejected:
        logger.warning("domain_graph_rejected_unknown_node_ids", count=rejected)
    return flows
