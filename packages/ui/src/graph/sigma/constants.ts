import { LANGUAGE_COLORS, languageColor } from "../../lib/confidence";

// Re-export for convenience
export { LANGUAGE_COLORS, languageColor };

// ---- Community palette (24 perceptually-optimized colors for dark backgrounds) ----

export const COMMUNITY_COLORS = [
  "#FF6B6B", "#4ECDC4", "#FFE66D", "#AA96DA", "#F38181",
  "#A8E6CF", "#87CEEB", "#F4A460", "#98FB98", "#FF69B4",
  "#20B2AA", "#FFA07A", "#9370DB", "#3CB371", "#FF7F50",
  "#6495ED", "#DAA520", "#00CED1", "#FF1493", "#32CD32",
  "#BA55D3", "#FF8C00", "#7B68EE", "#48D1CC",
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
    ? 5 * baseMassMultiplier
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
    gravity: isSmall ? 0.8 : isMedium ? 0.5 : isLarge ? 0.3 : 0.2,
    scalingRatio: isSmall ? 12 : isMedium ? 20 : isLarge ? 30 : 40,
    slowDown: isSmall ? 10 : isMedium ? 12 : isLarge ? 15 : 20,
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
  if (nodeCount > 10000) return 20000;
  if (nodeCount > 5000) return 15000;
  if (nodeCount > 2000) return 12000;
  if (nodeCount > 1000) return 10000;
  if (nodeCount > 500) return 8000;
  return 5000;
}

// ---- Noverlap post-layout settings ----

export const NOVERLAP_SETTINGS = {
  maxIterations: 20,
  ratio: 1.1,
  margin: 10,
  expansion: 1.05,
} as const;

// ---- Edge rendering thresholds ----

export const CURVED_EDGE_THRESHOLD = 3000;

// ---- Label rendering ----

export const LABEL_FONT = "JetBrains Mono, ui-monospace, monospace";
export const LABEL_SIZE = 11;
export const LABEL_DENSITY = 0.15;
export const LABEL_GRID_CELL_SIZE = 80;
export const LABEL_RENDERED_SIZE_THRESHOLD = 6;

// ---- Surface depth system (for panels, tooltips, controls) ----

export const SURFACE_COLORS = {
  void: "#06060a",
  deep: "#0a0a10",
  surface: "#101018",
  elevated: "#16161f",
  hover: "#1c1c28",
} as const;
