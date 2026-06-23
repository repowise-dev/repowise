"""Async orchestrator for domain-graph synthesis.

Ties the pure stages together with a fixed, bounded LLM budget: one call to
name domains, then one call per domain to extract its flows/steps (skipped for
any domain whose structural fingerprint is unchanged from a prior run). The
budget is a function of the (small) domain count, never the file count.

Failures degrade gracefully: a failed naming call yields an empty graph, a
failed flow call drops only that domain. The caller wires this into
``enrich_knowledge_graph`` inside a try/except so it can never block export.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from . import context
from .cache import domain_fingerprint, reuse_flows
from .models import DomainGraph, DomainNode
from .parsing import parse_domains, resolve_flows
from .prompts import (
    DOMAIN_NAMING_SYSTEM,
    FLOW_EXTRACTION_SYSTEM,
    build_domain_naming_prompt,
    build_flow_prompt,
)

logger = structlog.get_logger(__name__)


async def synthesize_domain_graph(
    layers: list[dict],
    nodes: list[dict],
    edges: list[dict],
    llm_client: Any,
    *,
    page_summaries: dict[str, str] | None = None,
    prior: DomainGraph | None = None,
    reasoning: str = "auto",
) -> DomainGraph:
    """Synthesize the behavior-oriented domain graph from structural inputs."""
    page_summaries = page_summaries or {}
    clusters = context.build_layer_clusters(layers, nodes)
    if not clusters:
        return DomainGraph()

    valid_layer_ids = {c.layer_id for c in clusters}
    naming_prompt = build_domain_naming_prompt(clusters)
    try:
        response = await llm_client.generate(
            DOMAIN_NAMING_SYSTEM,
            naming_prompt,
            max_tokens=2048,
            temperature=0.3,
            reasoning=reasoning,
        )
    except Exception as exc:
        logger.warning("domain_graph_naming_failed", error=str(exc))
        return DomainGraph()

    domains = parse_domains(response.content, valid_layer_ids)
    if not domains:
        logger.warning("domain_graph_no_domains_parsed")
        return DomainGraph()

    # Resolve member files + fingerprint each domain so unchanged domains can
    # reuse cached flows instead of spending an LLM call.
    summary_by_id = {
        nid: context._best_summary(node, page_summaries)
        for nid, node in context.file_nodes_by_id(nodes).items()
    }
    for domain in domains:
        domain.member_node_ids = context.member_node_ids(
            domain.member_layer_ids, layers, nodes
        )
        internal = context.internal_edge_ids(set(domain.member_node_ids), edges)
        domain.fingerprint = domain_fingerprint(
            domain.member_node_ids, summary_by_id, internal
        )

    await asyncio.gather(
        *(
            _fill_flows(domain, nodes, page_summaries, edges, llm_client, prior, reasoning)
            for domain in domains
        )
    )

    # A domain with no extractable flow is an orphan; drop it so the persisted
    # graph satisfies the reviewer's no-orphan invariant.
    surviving = [d for d in domains if d.flows]
    dropped = len(domains) - len(surviving)
    if dropped:
        logger.info("domain_graph_dropped_orphan_domains", count=dropped)

    cross = context.derive_cross_domain_edges(surviving, edges)
    logger.info(
        "domain_graph_synthesized",
        domains=len(surviving),
        flows=sum(len(d.flows) for d in surviving),
        cross_domain_edges=len(cross),
    )
    return DomainGraph(domains=surviving, cross_domain=cross)


async def _fill_flows(
    domain: DomainNode,
    nodes: list[dict],
    page_summaries: dict[str, str],
    edges: list[dict],
    llm_client: Any,
    prior: DomainGraph | None,
    reasoning: str,
) -> None:
    """Populate one domain's flows, reusing the cache or making one LLM call."""
    cached = reuse_flows(prior, domain)
    if cached is not None:
        domain.flows = cached
        logger.debug("domain_graph_flows_reused", domain=domain.slug)
        return

    members = context.build_member_context(domain, nodes, page_summaries)
    internal = context.heaviest_internal_edges(set(domain.member_node_ids), edges)
    prompt = build_flow_prompt(domain.name, domain.summary, members, internal)
    try:
        response = await llm_client.generate(
            FLOW_EXTRACTION_SYSTEM,
            prompt,
            max_tokens=3000,
            temperature=0.3,
            reasoning=reasoning,
        )
    except Exception as exc:
        logger.warning("domain_graph_flow_failed", domain=domain.slug, error=str(exc))
        return
    domain.flows = resolve_flows(response.content, set(domain.member_node_ids))
