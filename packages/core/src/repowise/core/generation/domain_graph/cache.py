"""Per-domain structural fingerprint + reuse (mirrors page-cache behaviour).

A domain's flows/steps only need re-synthesizing when its member set changes
structurally. The fingerprint hashes the member node ids together with their
best summaries (which themselves change only when the underlying source does),
so an unchanged domain can reuse the prior run's flows without an LLM call.
Pure - no DB, no LLM.
"""

from __future__ import annotations

import hashlib

from .models import DomainGraph, DomainNode, FlowNode


def domain_fingerprint(
    member_node_ids: list[str],
    summary_by_node_id: dict[str, str],
    internal_edges: list[tuple[str, str]] = (),
) -> str:
    """Stable hash over the members (id + summary) and their internal coupling.

    Folding the internal import edges in means a coupling change among
    otherwise-unchanged member files still invalidates the cache, so reused
    flows can never reflect stale structure.
    """
    hasher = hashlib.sha256()
    for nid in sorted(member_node_ids):
        hasher.update(nid.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update((summary_by_node_id.get(nid, "")).encode("utf-8"))
        hasher.update(b"\x01")
    hasher.update(b"\x02")
    for src, tgt in sorted(internal_edges):
        hasher.update(src.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(tgt.encode("utf-8"))
        hasher.update(b"\x01")
    return hasher.hexdigest()


def reuse_flows(
    prior: DomainGraph | None,
    domain: DomainNode,
) -> list[FlowNode] | None:
    """Return the prior run's flows for *domain* when its fingerprint matches.

    Matched by slug + fingerprint; ``None`` means "re-synthesize". A prior
    domain with an empty fingerprint never matches (forces a refresh).
    """
    if prior is None or not domain.fingerprint:
        return None
    for prev in prior.domains:
        if prev.slug == domain.slug and prev.fingerprint == domain.fingerprint and prev.flows:
            return [FlowNode.from_dict(f.to_dict()) for f in prev.flows]
    return None
