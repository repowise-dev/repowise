/**
 * Pure transform: collapse the service-granular system graph into a
 * repo-granular one. Services in the same repo merge into a single repo node;
 * intra-repo edges drop out; cross-repo edges sharing a (source, target, kind)
 * merge with summed weight and the strongest match type. Used by the map's
 * "collapse to repos" toggle. Side-effect free and cheap to test.
 */

import type { SystemEdge, SystemEdgeMatchType, SystemGraph, SystemNode } from "@repowise-dev/types";

/** Confidence ordering for picking the strongest match type when merging. */
const MATCH_RANK: Record<SystemEdgeMatchType, number> = {
  exact: 3,
  manual: 2,
  candidate: 1,
  inferred: 0,
};

/** Cap on retained drill-down refs per merged edge (mirrors the core bound). */
const MAX_REFS = 50;

function strongerMatch(a: SystemEdgeMatchType, b: SystemEdgeMatchType): SystemEdgeMatchType {
  return MATCH_RANK[a] >= MATCH_RANK[b] ? a : b;
}

function repoNode(repo: string, members: SystemNode[]): SystemNode {
  const contractTypes = new Set<string>();
  let providerCount = 0;
  let consumerCount = 0;
  let orphanProvider = false;
  let orphanConsumer = false;
  for (const m of members) {
    providerCount += m.provider_count;
    consumerCount += m.consumer_count;
    m.contract_types.forEach((t) => contractTypes.add(t));
    orphanProvider = orphanProvider || m.is_orphan_provider;
    orphanConsumer = orphanConsumer || m.is_orphan_consumer;
  }
  // A repo node's kind: keep a single service kind unless every member is a
  // library or external (then surface that), so the collapsed view stays honest.
  const kinds = new Set(members.map((m) => m.kind));
  const kind: SystemNode["kind"] =
    kinds.size === 1 ? (members[0]?.kind ?? "service") : "service";
  return {
    id: repo,
    repo,
    service_path: null,
    name: repo,
    kind,
    provider_count: providerCount,
    consumer_count: consumerCount,
    contract_types: [...contractTypes].sort(),
    is_orphan_provider: orphanProvider,
    is_orphan_consumer: orphanConsumer,
    is_isolated: false, // recomputed below from the collapsed edge set
  };
}

export function collapseToRepos(graph: SystemGraph): SystemGraph {
  const repoOf = new Map<string, string>();
  const membersByRepo = new Map<string, SystemNode[]>();
  for (const node of graph.nodes) {
    repoOf.set(node.id, node.repo);
    const list = membersByRepo.get(node.repo) ?? [];
    list.push(node);
    membersByRepo.set(node.repo, list);
  }

  const nodes = [...membersByRepo.entries()]
    .map(([repo, members]) => repoNode(repo, members))
    .sort((a, b) => a.id.localeCompare(b.id));

  const merged = new Map<string, SystemEdge>();
  for (const edge of graph.edges) {
    const source = repoOf.get(edge.source) ?? edge.source;
    const target = repoOf.get(edge.target) ?? edge.target;
    if (source === target) continue; // intra-repo edge disappears on collapse
    const key = `${source}->${target}::${edge.kind}`;
    const existing = merged.get(key);
    if (!existing) {
      merged.set(key, {
        ...edge,
        id: key,
        source,
        target,
        contract_refs: edge.contract_refs.slice(0, MAX_REFS),
      });
      continue;
    }
    existing.weight += edge.weight;
    existing.confidence = Math.max(existing.confidence, edge.confidence);
    existing.match_type = strongerMatch(existing.match_type, edge.match_type);
    if (existing.contract_refs.length < MAX_REFS) {
      existing.contract_refs = [...existing.contract_refs, ...edge.contract_refs].slice(0, MAX_REFS);
    }
  }

  const edges = [...merged.values()].sort((a, b) => a.id.localeCompare(b.id));
  const connected = new Set<string>();
  for (const e of edges) {
    connected.add(e.source);
    connected.add(e.target);
  }
  for (const n of nodes) n.is_isolated = !connected.has(n.id);

  return {
    version: graph.version,
    generated_at: graph.generated_at,
    nodes,
    edges,
    diagnostics: graph.diagnostics,
  };
}
