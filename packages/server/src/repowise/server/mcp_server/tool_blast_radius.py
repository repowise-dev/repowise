"""MCP Tool: get_blast_radius — cross-repo downstream impact (workspace only).

Answers "what structural or historical reach follows from this service?" by
traversing the workspace system graph. Structural dependencies (contracts,
package deps) are ranked above behavioral co-change. Mirrors the single-repo
change-risk vocabulary (``get_risk`` PR-mode, ``blast-radius.ts``): impacted
services carry an impact ``score`` and a ``distance``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _is_workspace_mode
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import persisted_analysis_meta as _analysis_meta

#: How many impacted services the MCP response carries inline. The full set is
#: available via the REST endpoint / the map; here we keep the agent payload
#: tight and report the true count in ``total_impacted``.
_MCP_IMPACTED_LIMIT = 25

#: How many symbol-level consumers each symbol target carries inline. The
#: service-level ``impacted`` list already covers the breadth; this block is the
#: precise hop, so it stays short.
_SYMBOL_CONSUMER_LIMIT = 10


@mcp.tool(default=False, requires_workspace=True, surface_order=210, trust_kind="structural")
async def get_blast_radius(
    targets: list[str],
    max_depth: int = 3,
    include_behavioral: bool = True,
) -> dict[str, Any]:
    """Cross-repo blast radius — which downstream services are structurally reachable.

    Workspace-only. Traverses the system graph from the given service(s) and
    returns the reachable services across every repo, ranked by an uncalibrated
    0-1 path-weight heuristic. It is not runtime-breakage evidence or a
    probability.
    Structural edges (http / grpc / event / package) outweigh behavioral
    co-change. Call before changing a high-fan-out provider to see who consumes
    it across repo boundaries.

    A target may also be a symbol id ("path/to/file.ts::Name"). It resolves to
    the service that publishes that symbol, and ``symbol_targets`` additionally
    names the consuming *symbols* on the far side of each contract link, which
    is the one hop the service-level graph cannot express.

    Args:
        targets: node ids ("repo" or "repo::service/path"), repo aliases, or
            symbol ids of a published symbol.
        max_depth: reachability depth (1-8, default 3).
        include_behavioral: include co-change (behavioral) edges (default true).
    """
    if not _is_workspace_mode():
        return {
            "error": "get_blast_radius is only available in workspace mode.",
            "_meta": _build_meta(),
        }

    enricher = _state._cross_repo_enricher
    raw = enricher.get_system_graph() if enricher is not None else None
    if not raw:
        return {
            "error": (
                "No system graph is available yet. Run `repowise update --workspace` "
                "to build cross-repo relationships."
            ),
            "_meta": _build_meta(),
        }

    from repowise.core.analysis.risk_semantics import workspace_impact_score_semantics
    from repowise.core.workspace.blast_radius import cross_repo_blast_radius, resolve_targets
    from repowise.core.workspace.system_graph import SystemGraph

    graph = SystemGraph.from_dict(raw)
    # Only targets the node/alias resolver rejected are tried as symbol ids, so
    # a string that already names a node keeps its existing meaning.
    _, unresolved = resolve_targets(graph, targets)
    symbol_targets, effective = _resolve_symbol_targets(enricher, graph, targets, unresolved)
    result = cross_repo_blast_radius(
        graph,
        effective,
        max_depth=max(1, min(max_depth, 8)),
        include_behavioral=include_behavioral,
    )

    impacted = [n.to_dict() for n in result.impacted[:_MCP_IMPACTED_LIMIT]]

    summary = (
        f"Changing {len(result.targets)} service(s) impacts {result.total_impacted} "
        f"downstream service(s) across {len(result.impacted_repos)} other repo(s): "
        f"{result.structural_count} via a real dependency, "
        f"{result.behavioral_count} via co-change only."
    )
    if not result.targets:
        summary = (
            f"None of the requested targets matched a service in the graph: "
            f"{result.unresolved_targets}."
        )
    # After the override: an unmatched-targets response must not claim consumers.
    if symbol_targets and result.targets:
        consumers = sum(t["consumer_count"] for t in symbol_targets)
        summary += (
            f" {len(symbol_targets)} symbol target(s) have {consumers} "
            f"consumer(s) across the contract links."
        )

    payload = {
        "targets": result.targets,
        "target_repos": result.target_repos,
        "impacted": impacted,
        # Against the true total: result.impacted is itself already capped.
        "impacted_truncated": max(0, result.total_impacted - len(impacted)),
        "impacted_repos": result.impacted_repos,
        "structural_count": result.structural_count,
        "behavioral_count": result.behavioral_count,
        "max_distance": result.max_distance,
        "total_impacted": result.total_impacted,
        "unresolved_targets": result.unresolved_targets,
        "impact_score_semantics": workspace_impact_score_semantics(),
        "summary": summary,
        "_meta": _build_meta(
            extra=_analysis_meta(
                raw.get("generated_at"),
                {
                    alias: provenance.get("head")
                    for alias, provenance in raw.get("repo_provenance", {}).items()
                    if provenance.get("head")
                },
            )
        ),
    }
    if symbol_targets:
        payload["symbol_targets"] = symbol_targets
    return payload


def _graph_node_for(nodes_by_repo: dict[str, list[Any]], contract: dict[str, Any]) -> str | None:
    """The system-graph node that carries *contract*, or ``None``.

    Resolved by longest service-path prefix over the graph's own nodes, the
    inverse of the ``assign_service`` walk that built them. ``Contract.service``
    is a different derivation and does not always name a node.
    """
    path = (contract.get("file_path") or "").replace("\\", "/")
    best: str | None = None
    best_len = -1
    for node in nodes_by_repo.get(contract.get("repo") or "", ()):
        service = node.service_path
        if service is None:
            if best_len < 0:
                best, best_len = node.id, 0
        elif path.startswith(service + "/") and len(service) > best_len:
            best, best_len = node.id, len(service)
    return best


def _resolve_symbol_targets(
    enricher: Any,
    graph: Any,
    targets: list[str],
    unresolved: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Expand symbol-id targets into graph nodes plus their symbol-level consumers.

    Returns the per-symbol blocks and the target list to traverse from: the
    caller's targets with each matched symbol id swapped for the node(s) that
    publish it. A symbol id matching nothing is left in place, so it still lands
    in the result's ``unresolved_targets``.
    """
    blocks: list[dict[str, Any]] = []
    replacements: dict[str, list[str]] = {}
    nodes_by_repo: dict[str, list[Any]] = defaultdict(list)
    for node in graph.nodes:
        nodes_by_repo[node.repo].append(node)
    for raw in dict.fromkeys(unresolved):
        # Providers only: a consumer contract's symbol calls a surface rather
        # than publishing one, so it has no downstream to traverse.
        contracts = [
            c for c in enricher.get_contracts_for_symbol(raw) if c.get("role") == "provider"
        ]
        nodes = sorted({node for c in contracts if (node := _graph_node_for(nodes_by_repo, c))})
        if not nodes:
            continue
        # Both indexes are repo-blind, so scope the links to the repos actually
        # traversed; otherwise the count includes a namesake's consumers.
        repos = sorted({c["repo"] for c in contracts if c.get("repo")})
        links = [
            lk
            for lk in enricher.get_contract_links_by_provider_symbol(raw)
            if lk.get("provider_repo") in set(repos)
        ]
        consumers = [
            {
                "provider_repo": link.get("provider_repo"),
                "repo": link.get("consumer_repo"),
                "file": link.get("consumer_file"),
                "contract_id": link.get("contract_id"),
                "contract_type": link.get("contract_type"),
                "match_type": link.get("match_type"),
                "confidence": link.get("confidence"),
                # An import sits at file scope, so code contracts carry none.
                **({"symbol_id": sid} if (sid := link.get("consumer_symbol_id")) else {}),
            }
            for link in links[:_SYMBOL_CONSUMER_LIMIT]
        ]
        block: dict[str, Any] = {
            "symbol_id": raw,
            "nodes": nodes,
            "contract_ids": sorted({c["contract_id"] for c in contracts if c.get("contract_id")}),
            "consumers": consumers,
            "consumer_count": len(links),
            "consumers_truncated": max(0, len(links) - len(consumers)),
        }
        # Symbol ids are repo-relative, so one id can name two symbols.
        if len(repos) > 1:
            block["ambiguous_in_repos"] = repos
        blocks.append(block)
        replacements[raw] = nodes

    if not replacements:
        return [], targets
    effective: list[str] = []
    for raw in targets:
        effective.extend(replacements.get(raw, [raw]))
    return blocks, effective
