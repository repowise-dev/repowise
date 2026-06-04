/**
 * Constellation adapter — turns the /architecture community super-graph
 * into a Graphology graph laid out radially (see radial-layout.ts).
 *
 * One node per community ("hub"), one dark repo-core at the origin, and
 * aggregated cross-community edges. NO physics: positions come straight
 * from the deterministic radial layout. Colors are placeholders here; the
 * theme-aware recolor in use-sigma is the source of truth (canvas can't
 * resolve var()), exactly like the file/module adapters.
 */

import Graph from "graphology";
import type { ArchitectureGraph, ArchitectureNode } from "@repowise-dev/types/graph";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";
import { EDGE_COLORS } from "./constants";
import { computeRadialLayout, hubSizeFromMembers } from "./radial-layout";

/** Stable hub node id derived from the community id. */
export function hubNodeId(communityId: number): string {
  return `__community__${communityId}`;
}

/** The single repo-core node id. */
export const CORE_NODE_ID = "__repo_core__";

// Neutral build-time fills; recolored per theme by use-sigma's color effect.
const PLACEHOLDER_HUB_COLOR = EDGE_COLORS.crossCommunity;
const PLACEHOLDER_CORE_COLOR = "#1a1320";

/** Uppercase, trimmed community label; falls back to dirname / "Community N". */
function hubLabel(node: ArchitectureNode): string {
  const raw = (node.label ?? "").trim();
  if (raw) return raw.toUpperCase();
  const fromFile = (node.top_file ?? "").split("/").slice(-2, -1)[0];
  if (fromFile) return fromFile.toUpperCase();
  return `COMMUNITY ${node.community_id}`;
}

/** Curvature mirrors the file/module adapters (deterministic per edge key). */
function computeEdgeCurvature(edgeKey: string): number {
  let hash = 5381;
  for (let i = 0; i < edgeKey.length; i++) {
    hash = ((hash << 5) + hash + (edgeKey.charCodeAt(i) ?? 0)) | 0;
  }
  return 0.12 + (Math.abs(hash) % 80) / 1000;
}

export interface ConstellationOptions {
  /** Repo name for the core label. Falls back to "REPO". */
  repoName?: string;
  /** Add faint hub→core spokes. Off by default (can read as noise). */
  includeCoreSpokes?: boolean;
}

/**
 * Build the constellation graphology graph from the architecture payload.
 * Pure + deterministic. Empty/one-community repos render what exists.
 */
export function architectureToGraphology(
  arch: ArchitectureGraph,
  options?: ConstellationOptions,
): Graph<SigmaNodeAttributes, SigmaEdgeAttributes> {
  const result = new Graph<SigmaNodeAttributes, SigmaEdgeAttributes>();

  const layout = computeRadialLayout(
    arch.nodes.map((n) => ({
      community_id: n.community_id,
      member_count: n.member_count,
      avg_pagerank: n.avg_pagerank,
    })),
  );

  // Repo-core: dark plum disc at the origin, largest size, fixed.
  result.addNode(CORE_NODE_ID, {
    x: layout.core.x,
    y: layout.core.y,
    size: 40,
    color: PLACEHOLDER_CORE_COLOR,
    label: (options?.repoName || "REPO").toUpperCase(),
    nodeType: "core",
    fullPath: "",
    language: "",
    communityId: -1,
    pagerank: 0,
    betweenness: 0,
    isTest: false,
    isEntryPoint: false,
    hasDoc: false,
    symbolCount: 0,
    forceLabel: true,
    zIndex: 3,
    originalColor: PLACEHOLDER_CORE_COLOR,
  });

  // One hub disc per community.
  for (const node of arch.nodes) {
    const pos = layout.hubs.get(node.community_id);
    if (!pos) continue;
    const size = hubSizeFromMembers(node.member_count);
    result.addNode(hubNodeId(node.community_id), {
      x: pos.x,
      y: pos.y,
      size,
      color: PLACEHOLDER_HUB_COLOR,
      label: hubLabel(node),
      nodeType: "hub",
      fullPath: node.top_file ?? "",
      language: node.languages?.[0] ?? "",
      communityId: node.community_id,
      pagerank: node.avg_pagerank,
      betweenness: 0,
      isTest: false,
      isEntryPoint: false,
      hasDoc: node.doc_coverage_pct > 0,
      symbolCount: 0,
      memberCount: node.member_count,
      hotspotCount: node.hotspot_count,
      deadCount: node.dead_count,
      docCoveragePct: node.doc_coverage_pct,
      languages: node.languages ?? [],
      hasDecision: node.has_decision,
      forceLabel: true,
      zIndex: 2,
      originalColor: PLACEHOLDER_HUB_COLOR,
    });
  }

  // Aggregated cross-community edges: thin plum spokes beneath the hubs.
  const maxEdgeCount = arch.edges.reduce((m, e) => Math.max(m, e.edge_count), 1);
  for (const edge of arch.edges) {
    const src = hubNodeId(edge.source);
    const tgt = hubNodeId(edge.target);
    if (!result.hasNode(src) || !result.hasNode(tgt)) continue;
    const edgeKey = src + "→" + tgt;
    if (result.hasEdge(edgeKey)) continue;
    // 1.5–2.5px proportional to edge_count.
    const size = 1.5 + (edge.edge_count / maxEdgeCount);
    result.addEdgeWithKey(edgeKey, src, tgt, {
      size,
      color: EDGE_COLORS.crossCommunity,
      type: "curved",
      curvature: computeEdgeCurvature(edgeKey),
      edgeKind: "crossCommunity",
      importedNames: [],
      edgeCount: edge.edge_count,
      zIndex: 0,
    });
  }

  // Optional faint hub→core spokes (composition aid; off by default).
  if (options?.includeCoreSpokes) {
    for (const node of arch.nodes) {
      const src = hubNodeId(node.community_id);
      if (!result.hasNode(src)) continue;
      const edgeKey = src + "→" + CORE_NODE_ID;
      if (result.hasEdge(edgeKey)) continue;
      result.addEdgeWithKey(edgeKey, src, CORE_NODE_ID, {
        size: 0.6,
        color: EDGE_COLORS.lowConfidence,
        type: "line",
        curvature: 0,
        edgeKind: "lowConfidence",
        importedNames: [],
        edgeCount: 1,
        zIndex: 0,
      });
    }
  }

  return result;
}
