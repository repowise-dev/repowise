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
import type {
  ArchitectureGraph,
  ArchitectureNode,
  CommunitySlice,
} from "@repowise-dev/types/graph";
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

/** Satellite (member file) node size in sigma units, ∝ pagerank, clamped 6-14
 *  to match the file-node sizing band used elsewhere in the graph. */
export function satelliteSizeFromPagerank(pagerank: number): number {
  return Math.max(6, Math.min(14, 6 + pagerank * 16));
}

/** Result of merging a slice into the constellation: the (mutated) graph plus
 *  the satellite node ids that were added, for camera framing + collapse. */
export interface SliceMergeResult {
  satelliteIds: string[];
}

/**
 * Blossom a community's member files as satellites around its hub, using the
 * SAME deterministic satellite math as the initial layout (golden-angle spiral,
 * hash jitter, in-cluster noverlap) — no FA2. Mutates *graph* in place: adds
 * file nodes for members, thin intra-community edges, faint membership spokes
 * for members with no intra edge, plus minimal boundary stubs + cross-cluster
 * edges to already-visible hubs.
 *
 * Idempotent per community: re-merging the same slice skips existing nodes.
 * Colors are placeholders (recolored per theme by use-sigma); satellites are
 * `nodeType:"file"` so the community-mode recolor paints them the family SOFT
 * variant automatically.
 */
export function mergeCommunitySlice(
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes>,
  communityId: number,
  slice: CommunitySlice,
): SliceMergeResult {
  const hubId = hubNodeId(communityId);
  if (!graph.hasNode(hubId)) return { satelliteIds: [] };
  const hub = graph.getNodeAttributes(hubId);

  const members = slice.nodes.filter((n) => !n.is_boundary);
  const boundary = slice.nodes.filter((n) => n.is_boundary);
  const memberIds = new Set(members.map((m) => m.node_id));

  // Deterministic satellite positions around the hub. We reuse computeRadialLayout
  // with a single synthetic community sized to this slice, so the spiral/jitter/
  // noverlap exactly mirror the layout module — then translate onto the hub.
  const memberCount = hub.memberCount ?? members.length;
  const layout = computeRadialLayout(
    [{ community_id: communityId, member_count: memberCount, avg_pagerank: hub.pagerank ?? 0 }],
    new Map([[communityId, members.map((m) => m.node_id)]]),
  );
  // The synthetic layout centers the hub at its own ring position; shift the
  // satellites so they sit around THIS hub's actual position.
  const synthHub = layout.hubs.get(communityId) ?? { x: 0, y: 0 };
  const dx = hub.x - synthHub.x;
  const dy = hub.y - synthHub.y;

  const satelliteIds: string[] = [];

  for (const m of members) {
    if (graph.hasNode(m.node_id)) {
      satelliteIds.push(m.node_id);
      continue;
    }
    const pos = layout.satellites.get(m.node_id) ?? synthHub;
    const baseSize = satelliteSizeFromPagerank(m.pagerank ?? 0);
    graph.addNode(m.node_id, {
      x: pos.x + dx,
      y: pos.y + dy,
      size: baseSize,
      color: EDGE_COLORS.crossCommunity, // placeholder; recolored to family soft
      label: m.node_id.split("/").pop() ?? m.node_id,
      nodeType: "file",
      fullPath: m.node_id,
      language: m.language,
      communityId,
      pagerank: m.pagerank ?? 0,
      betweenness: m.betweenness ?? 0,
      isTest: m.is_test ?? false,
      isEntryPoint: m.is_entry_point ?? false,
      hasDoc: m.has_doc ?? false,
      symbolCount: m.symbol_count ?? 0,
      isHotspot: m.is_hotspot ?? false,
      isDead: m.is_dead ?? false,
      hasDecision: m.has_decision ?? false,
      primaryOwner: m.primary_owner ?? null,
      zIndex: 1,
      originalColor: EDGE_COLORS.crossCommunity,
    });
    satelliteIds.push(m.node_id);
  }

  // Boundary stubs: tiny, rendered only so cross-cluster edges have an endpoint.
  // We DROP visual prominence (size ~3) and only keep ones whose hub is already
  // visible OR that bridge to another expanded member; otherwise the stub anchors
  // near this hub. (Chosen: render tiny rather than omit, so the cross link is
  // visibly anchored — documented in the report.)
  for (const b of boundary) {
    if (graph.hasNode(b.node_id)) continue;
    graph.addNode(b.node_id, {
      x: hub.x + (((b.node_id.length * 7) % 40) - 20),
      y: hub.y + (((b.node_id.length * 13) % 40) - 20),
      size: 3,
      color: EDGE_COLORS.lowConfidence,
      label: "",
      nodeType: "file",
      fullPath: b.node_id,
      language: b.language,
      communityId: b.community_id,
      pagerank: 0,
      betweenness: 0,
      isTest: false,
      isEntryPoint: false,
      hasDoc: false,
      symbolCount: 0,
      zIndex: 0,
      originalColor: EDGE_COLORS.lowConfidence,
    });
  }

  // Edges among members (thin internal lines) + cross-cluster links to stubs.
  // Track which members got at least one intra edge so the rest get a spoke.
  const hasIntraEdge = new Set<string>();
  for (const link of slice.links) {
    const srcIn = memberIds.has(link.source);
    const tgtIn = memberIds.has(link.target);
    if (!graph.hasNode(link.source) || !graph.hasNode(link.target)) continue;
    const key = `slice:${link.source}→${link.target}`;
    if (graph.hasEdge(key)) continue;
    if (srcIn && tgtIn) {
      hasIntraEdge.add(link.source);
      hasIntraEdge.add(link.target);
      graph.addEdgeWithKey(key, link.source, link.target, {
        size: 0.5,
        color: EDGE_COLORS.internal,
        type: "line",
        curvature: 0,
        edgeKind: "internal", // thin internal hue (theme recolor is source of truth)
        importedNames: link.imported_names ?? [],
        edgeCount: 1,
        zIndex: 0,
      });
    } else {
      // Cross-cluster: member <-> outside boundary stub.
      graph.addEdgeWithKey(key, link.source, link.target, {
        size: 0.6,
        color: EDGE_COLORS.crossCommunity,
        type: "line",
        curvature: 0,
        edgeKind: "crossCommunity",
        importedNames: link.imported_names ?? [],
        edgeCount: 1,
        zIndex: 0,
      });
    }
  }

  // Synthetic membership spokes: members with no intra edge still tie to the hub
  // so the cluster reads as a wheel, not dust (faint lowConfidence hue).
  for (const m of members) {
    if (hasIntraEdge.has(m.node_id)) continue;
    const key = `spoke:${hubId}→${m.node_id}`;
    if (graph.hasEdge(key)) continue;
    graph.addEdgeWithKey(key, hubId, m.node_id, {
      size: 0.4,
      color: EDGE_COLORS.lowConfidence,
      type: "line",
      curvature: 0,
      edgeKind: "lowConfidence",
      importedNames: [],
      edgeCount: 1,
      zIndex: 0,
    });
  }

  return { satelliteIds };
}
