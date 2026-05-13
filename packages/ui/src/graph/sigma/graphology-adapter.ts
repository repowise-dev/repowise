import Graph from "graphology";
import type {
  GraphExport,
  GraphNode,
  GraphLink,
  ModuleGraph,
  ModuleNode,
  ModuleEdge,
  CommunitySummaryItem,
} from "@repowise-dev/types/graph";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";
import {
  NODE_BASE_SIZES,
  EDGE_COLORS,
  EDGE_SIZE_MULTIPLIERS,
  CURVED_EDGE_THRESHOLD,
  getScaledNodeSize,
  getNodeMass,
  getCommunityColor,
  languageColor,
} from "./constants";
import { groupNodesAsModules } from "../elk-layout";

function simpleHash(str: string): number {
  let hash = 5381;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) + hash + (str.charCodeAt(i) ?? 0)) | 0;
  }
  return Math.abs(hash);
}

function classifyEdge(
  link: GraphLink,
  nodeMap: Map<string, GraphNode>,
): SigmaEdgeAttributes["edgeKind"] {
  if (link.confidence !== undefined && link.confidence < 0.5)
    return "lowConfidence";
  if (link.imported_names.length === 0) return "dynamic";
  const sourceNode = nodeMap.get(link.source);
  const targetNode = nodeMap.get(link.target);
  if (
    sourceNode &&
    targetNode &&
    sourceNode.community_id === targetNode.community_id
  )
    return "internal";
  if (
    sourceNode &&
    targetNode &&
    sourceNode.community_id !== targetNode.community_id
  )
    return "crossCommunity";
  return "import";
}

function computeEdgeSize(
  edgeKind: SigmaEdgeAttributes["edgeKind"],
  nodeCount: number,
): number {
  const baseScale =
    nodeCount > 10000
      ? 0.15
      : nodeCount > 5000
        ? 0.25
        : nodeCount > 2000
          ? 0.35
          : nodeCount > 1000
            ? 0.5
            : nodeCount > 500
              ? 0.7
              : 1.0;
  return baseScale * EDGE_SIZE_MULTIPLIERS[edgeKind];
}

function smartLabel(fullPath: string): string {
  const parts = fullPath.split("/");
  if (parts.length >= 2) return parts.slice(-2).join("/");
  return parts[parts.length - 1] ?? fullPath;
}

function computeEdgeCurvature(edgeKey: string): number {
  const hash = simpleHash(edgeKey);
  return 0.12 + (hash % 80) / 1000;
}

export function fileGraphToGraphology(
  graph: GraphExport,
  options?: {
    signals?: { hotNodeIds?: Set<string>; deadNodeIds?: Set<string> };
    nodeCount?: number;
  },
): Graph<SigmaNodeAttributes, SigmaEdgeAttributes> {
  const result = new Graph<SigmaNodeAttributes, SigmaEdgeAttributes>();
  const nodeCount = options?.nodeCount ?? graph.nodes.length;

  // Build lookup maps
  const nodeMap = new Map<string, GraphNode>();
  const communityNodes = new Map<number, GraphNode[]>();
  for (const node of graph.nodes) {
    nodeMap.set(node.node_id, node);
    const list = communityNodes.get(node.community_id) ?? [];
    list.push(node);
    communityNodes.set(node.community_id, list);
  }

  // Warm-start positioning with golden-angle radial distribution
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const spread = Math.sqrt(nodeCount) * 40;
  const sortedCommunities = Array.from(communityNodes.keys()).sort(
    (a, b) => a - b,
  );
  const communityCount = sortedCommunities.length;
  const jitter = Math.sqrt(nodeCount) * 3;

  for (let i = 0; i < sortedCommunities.length; i++) {
    const communityId = sortedCommunities[i]!;
    const members = communityNodes.get(communityId)!;

    const angle = i * goldenAngle;
    const radius = spread * Math.sqrt((i + 1) / communityCount);
    const centroidX = radius * Math.cos(angle);
    const centroidY = radius * Math.sin(angle);

    for (const node of members) {
      const hash = simpleHash(node.node_id);
      const x = centroidX + ((hash % 1000) / 1000 - 0.5) * jitter;
      const y = centroidY + (((hash >> 10) % 1000) / 1000 - 0.5) * jitter;

      let baseSize: number;
      if (node.is_entry_point) {
        baseSize = NODE_BASE_SIZES.entryPoint;
      } else if (node.is_test) {
        baseSize = NODE_BASE_SIZES.test;
      } else {
        baseSize = NODE_BASE_SIZES.file;
      }
      let size = getScaledNodeSize(baseSize, nodeCount);
      size *= Math.min(1 + node.pagerank * 2, 2);

      const color = languageColor(node.language);

      const attrs: SigmaNodeAttributes = {
        x,
        y,
        size,
        color,
        label: smartLabel(node.node_id),
        nodeType: "file",
        fullPath: node.node_id,
        language: node.language,
        communityId: node.community_id,
        pagerank: node.pagerank,
        betweenness: node.betweenness,
        isTest: node.is_test,
        isEntryPoint: node.is_entry_point,
        hasDoc: node.has_doc,
        symbolCount: node.symbol_count,
        mass: getNodeMass("file", nodeCount),
        originalColor: color,
      };

      // Signal data may come from two sources: explicit overlay sets
      // (legacy unified-graph flow) or enriched node payloads from Phase A.
      // We OR them so adapters work with both backends.
      if (
        node.is_hotspot ||
        options?.signals?.hotNodeIds?.has(node.node_id)
      ) {
        attrs.isHotspot = true;
      }
      if (
        node.is_dead ||
        options?.signals?.deadNodeIds?.has(node.node_id)
      ) {
        attrs.isDead = true;
      }
      attrs.churnPercentile = node.churn_percentile ?? null;
      attrs.deadConfidence = node.dead_confidence ?? null;
      attrs.hasDecision = node.has_decision ?? false;
      attrs.primaryOwner = node.primary_owner ?? null;

      result.addNode(node.node_id, attrs);
    }
  }

  // Classify edges in one O(E) pass, then bucket by kind (avoids O(E log E) sort)
  const kindBuckets: Record<SigmaEdgeAttributes["edgeKind"], GraphLink[]> = {
    crossCommunity: [],
    import: [],
    internal: [],
    dynamic: [],
    lowConfidence: [],
  };
  const edgeKindMap = new Map<GraphLink, SigmaEdgeAttributes["edgeKind"]>();
  for (const link of graph.links) {
    const kind = classifyEdge(link, nodeMap);
    edgeKindMap.set(link, kind);
    kindBuckets[kind].push(link);
  }
  const orderedLinks = (
    kindBuckets.crossCommunity
      .concat(kindBuckets.import)
      .concat(kindBuckets.internal)
      .concat(kindBuckets.dynamic)
      .concat(kindBuckets.lowConfidence)
  );

  const maxEdgesPerNode = nodeCount > 1000 ? 25 : Infinity;
  const edgesPerSource = new Map<string, number>();

  for (const link of orderedLinks) {
    if (!result.hasNode(link.source) || !result.hasNode(link.target)) continue;
    const edgeKey = link.source + "→" + link.target;
    if (result.hasEdge(edgeKey)) continue;

    const srcCount = edgesPerSource.get(link.source) ?? 0;
    if (srcCount >= maxEdgesPerNode) continue;
    edgesPerSource.set(link.source, srcCount + 1);

    const edgeKind = edgeKindMap.get(link) ?? classifyEdge(link, nodeMap);

    const useCurved = nodeCount <= CURVED_EDGE_THRESHOLD;
    const edgeAttrs: SigmaEdgeAttributes = {
      size: computeEdgeSize(edgeKind, nodeCount),
      color: EDGE_COLORS[edgeKind],
      type: useCurved ? "curved" : "line",
      curvature: useCurved ? computeEdgeCurvature(edgeKey) : 0,
      edgeKind,
      importedNames: link.imported_names,
      edgeCount: 1,
    };

    if (link.confidence !== undefined) {
      edgeAttrs.confidence = link.confidence;
    }

    result.addEdgeWithKey(edgeKey, link.source, link.target, edgeAttrs);
  }

  return result;
}

export function moduleGraphToGraphology(
  graph: ModuleGraph,
  options?: {
    communities?: CommunitySummaryItem[];
    nodeCount?: number;
  },
): Graph<SigmaNodeAttributes, SigmaEdgeAttributes> {
  const result = new Graph<SigmaNodeAttributes, SigmaEdgeAttributes>();
  const nodeCount = options?.nodeCount ?? graph.nodes.length;

  // Build community lookup: map module_id to community_id
  const moduleCommunity = new Map<string, number>();
  if (options?.communities) {
    for (const community of options.communities) {
      for (const mod of graph.nodes) {
        if (community.top_file === mod.module_id ||
            community.top_file.startsWith(mod.module_id + "/")) {
          moduleCommunity.set(mod.module_id, community.community_id);
        }
      }
    }
  }
  // Fill in missing modules with deterministic hash
  for (const mod of graph.nodes) {
    if (!moduleCommunity.has(mod.module_id)) {
      moduleCommunity.set(mod.module_id, simpleHash(mod.module_id) % 24);
    }
  }

  // Group modules by community for warm-start positioning
  const communityModules = new Map<number, ModuleNode[]>();
  for (const mod of graph.nodes) {
    const cid = moduleCommunity.get(mod.module_id) ?? 0;
    const list = communityModules.get(cid) ?? [];
    list.push(mod);
    communityModules.set(cid, list);
  }

  // Grid-based warm-start: communities in a grid, modules jittered around centroids
  const sortedCommunities = Array.from(communityModules.keys()).sort(
    (a, b) => a - b,
  );
  const communityCount = sortedCommunities.length;
  const cols = Math.max(Math.ceil(Math.sqrt(communityCount)), 1);
  const cellSize = Math.sqrt(nodeCount) * 80;
  const jitter = cellSize * 0.3;

  for (let i = 0; i < sortedCommunities.length; i++) {
    const communityId = sortedCommunities[i]!;
    const members = communityModules.get(communityId)!;

    const col = i % cols;
    const row = Math.floor(i / cols);
    const centroidX = (col - (cols - 1) / 2) * cellSize;
    const centroidY = (row - (Math.ceil(communityCount / cols) - 1) / 2) * cellSize;

    for (const mod of members) {
      const x = centroidX + (Math.random() - 0.5) * jitter;
      const y = centroidY + (Math.random() - 0.5) * jitter;

      const baseSize = getScaledNodeSize(NODE_BASE_SIZES.module, nodeCount);
      const size = baseSize * (0.5 + Math.min(Math.log2(Math.max(mod.file_count, 1)) * 0.3, 1.5));
      const color = getCommunityColor(communityId);

      result.addNode(mod.module_id, {
        x,
        y,
        size,
        color,
        label: smartLabel(mod.module_id),
        nodeType: "module",
        fullPath: mod.module_id,
        language: "",
        communityId,
        pagerank: mod.avg_pagerank,
        betweenness: 0,
        isTest: false,
        isEntryPoint: false,
        hasDoc: mod.doc_coverage_pct > 0,
        symbolCount: mod.symbol_count,
        fileCount: mod.file_count,
        avgPagerank: mod.avg_pagerank,
        docCoveragePct: mod.doc_coverage_pct,
        dominantCommunityId: communityId,
        mass: getNodeMass("module", nodeCount),
        originalColor: color,
      });
    }
  }

  // Add edges
  for (const edge of graph.edges) {
    if (!result.hasNode(edge.source) || !result.hasNode(edge.target)) continue;
    const edgeKey = edge.source + "→" + edge.target;
    if (result.hasEdge(edgeKey)) continue;

    const sourceCid = moduleCommunity.get(edge.source) ?? 0;
    const targetCid = moduleCommunity.get(edge.target) ?? 0;
    const edgeKind: SigmaEdgeAttributes["edgeKind"] =
      sourceCid === targetCid ? "internal" : "crossCommunity";

    const baseScale =
      nodeCount > 5000 ? 0.4 : nodeCount > 1000 ? 0.6 : 1.0;

    result.addEdgeWithKey(edgeKey, edge.source, edge.target, {
      size:
        baseScale *
        EDGE_SIZE_MULTIPLIERS[edgeKind] *
        (1 + Math.log2(edge.edge_count)),
      color: EDGE_COLORS[edgeKind],
      type: "curved",
      curvature: computeEdgeCurvature(edgeKey),
      edgeKind,
      importedNames: [],
      edgeCount: edge.edge_count,
    });
  }

  return result;
}

export function groupFilesAsModules(
  graph: GraphExport,
  options?: { prefix?: string },
): Graph<SigmaNodeAttributes, SigmaEdgeAttributes> {
  const { moduleNodes, moduleEdges } = groupNodesAsModules(
    graph.nodes,
    graph.links,
    options?.prefix ?? "",
  );

  return moduleGraphToGraphology({ nodes: moduleNodes, edges: moduleEdges });
}
