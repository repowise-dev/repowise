import { LANGUAGE_COLORS, languageColor } from "../../lib/confidence";
import { FORCE_COMMUNITY_PALETTE } from "../force-layout";

// Re-export for convenience
export { LANGUAGE_COLORS, languageColor, FORCE_COMMUNITY_PALETTE };

// ---- Community palette (24 colors, matches graph-inspection-panel.tsx) ----

export const COMMUNITY_COLORS = [
  "#6366f1", "#ec4899", "#10b981", "#f59e0b", "#3b82f6", "#a855f7",
  "#14b8a6", "#f97316", "#84cc16", "#06b6d4", "#e11d48", "#8b5cf6",
  "#22c55e", "#eab308", "#0ea5e9", "#d946ef", "#ef4444", "#78716c",
  "#64748b", "#0891b2", "#059669", "#b45309", "#7c3aed", "#db2777",
];

export function getCommunityColor(communityId: number): string {
  return COMMUNITY_COLORS[communityId % COMMUNITY_COLORS.length] ?? "#6366f1";
}

// ---- Node base sizes ----

export const NODE_BASE_SIZES = {
  module: 16,
  file: 6,
  entryPoint: 10,
  test: 4,
} as const;

/**
 * Adaptive node sizing — scales down for large graphs to prevent overlap.
 */
export function getScaledNodeSize(baseSize: number, nodeCount: number): number {
  if (nodeCount > 10000) return Math.max(1, baseSize * 0.4);
  if (nodeCount > 5000) return Math.max(1.5, baseSize * 0.5);
  if (nodeCount > 2000) return Math.max(2, baseSize * 0.65);
  if (nodeCount > 500) return Math.max(2.5, baseSize * 0.8);
  return baseSize;
}

/**
 * FA2 mass by node type — modules are heavier to create clear spatial separation.
 */
export function getNodeMass(
  nodeType: "file" | "module",
  nodeCount: number,
): number {
  const baseMassMultiplier = nodeCount > 5000 ? 2 : nodeCount > 1000 ? 1.5 : 1;
  return nodeType === "module"
    ? 20 * baseMassMultiplier
    : 3 * baseMassMultiplier;
}

// ---- Edge colors by semantic type ----

export const EDGE_COLORS = {
  import: "#1d4ed8",
  crossCommunity: "#7c3aed",
  internal: "#2d5a3d",
  dynamic: "#6b7280",
  lowConfidence: "#475569",
} as const;

export const EDGE_SIZE_MULTIPLIERS = {
  import: 0.6,
  crossCommunity: 0.8,
  internal: 0.4,
  dynamic: 0.3,
  lowConfidence: 0.3,
} as const;

// ---- ForceAtlas2 adaptive settings ----

export function getFA2Settings(nodeCount: number): Record<string, unknown> {
  const isSmall = nodeCount < 500;
  const isMedium = nodeCount >= 500 && nodeCount < 2000;
  const isLarge = nodeCount >= 2000 && nodeCount < 10000;

  return {
    gravity: isSmall ? 0.8 : isMedium ? 0.5 : isLarge ? 0.3 : 0.15,
    scalingRatio: isSmall ? 15 : isMedium ? 30 : isLarge ? 60 : 100,
    slowDown: isSmall ? 1 : isMedium ? 2 : isLarge ? 3 : 5,
    barnesHutOptimize: nodeCount > 200,
    barnesHutTheta: isLarge ? 0.8 : 0.6,
    strongGravityMode: false,
    outboundAttractionDistribution: true,
    linLogMode: false,
    adjustSizes: true,
    edgeWeightInfluence: 1,
  };
}

/**
 * How long FA2 should run before stopping (ms).
 */
export function getLayoutDuration(nodeCount: number): number {
  if (nodeCount > 10000) return 45000;
  if (nodeCount > 5000) return 35000;
  if (nodeCount > 2000) return 30000;
  if (nodeCount > 1000) return 30000;
  if (nodeCount > 500) return 25000;
  return 20000;
}

// ---- Noverlap post-layout settings ----

export const NOVERLAP_SETTINGS = {
  maxIterations: 20,
  ratio: 1.1,
  margin: 10,
  expansion: 1.05,
} as const;

// ---- Label rendering ----

export const LABEL_FONT = "JetBrains Mono, ui-monospace, monospace";
export const LABEL_SIZE = 11;
export const LABEL_DENSITY = 0.1;
export const LABEL_GRID_CELL_SIZE = 70;
export const LABEL_RENDERED_SIZE_THRESHOLD = 8;

// ---- Surface depth system (for panels, tooltips, controls) ----

export const SURFACE_COLORS = {
  void: "#06060a",
  deep: "#0a0a10",
  surface: "#101018",
  elevated: "#16161f",
  hover: "#1c1c28",
} as const;
