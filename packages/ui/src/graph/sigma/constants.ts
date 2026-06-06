import { LANGUAGE_COLORS, languageColor } from "../../lib/confidence";

// Re-export for convenience
export { LANGUAGE_COLORS, languageColor };

// Community colors are no longer a static jewel-tone array. They live as the
// warm `--color-community-*` token pairs in styles/globals.css and are resolved
// at runtime (per theme) by getCommunityFamily in shared/use-theme-tokens.ts.
// use-sigma's color effect applies them so they track light/dark.

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

// ---- Edge colors by semantic type (warm theme palette) ----
//
// Per-theme literal hex (canvas can't resolve var()); mirrors the semantic
// edge palette in lib/confidence.ts EDGE_COLORS — import = brand orange,
// crossCommunity = plum (--color-accent-secondary), internal = sage/green
// (--color-success family). Dark variants are lifted to read on the near-black
// plum canvas. Resolve via edgeColorsForTheme(); kept literal + allowlisted.

export type EdgeKind =
  | "import"
  | "crossCommunity"
  | "internal"
  | "dynamic"
  | "lowConfidence";

export const EDGE_COLORS_BY_THEME: Record<
  "light" | "dark",
  Record<EdgeKind, string>
> = {
  light: {
    import: "#f59520", // brand orange
    crossCommunity: "#58436c", // plum (accent-secondary)
    internal: "#1d8155", // sage/green (success)
    dynamic: "#8c7f88", // muted text-tertiary
    lowConfidence: "#b8aeb3", // faint plum-gray
  },
  dark: {
    import: "#f59520", // brand orange
    crossCommunity: "#a98fc4", // plum-300 (accent-secondary dark)
    internal: "#34d399", // green (success dark)
    dynamic: "#786f84", // muted text-tertiary dark
    lowConfidence: "#544c5e", // faint plum-gray dark
  },
};

export function edgeColorsForTheme(
  theme: "light" | "dark",
): Record<EdgeKind, string> {
  return EDGE_COLORS_BY_THEME[theme] ?? EDGE_COLORS_BY_THEME.dark;
}

/**
 * Default edge palette for build-time adapter use before the theme-aware
 * recolor effect runs. Dark is the product default theme.
 */
export const EDGE_COLORS = EDGE_COLORS_BY_THEME.dark;

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
  if (nodeCount > 2000) return 12000;
  return 8000;
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

/** Sparser labels on large graphs to keep repaint cheap. */
export function getLabelDensity(nodeCount: number): number {
  return nodeCount > 2000 ? 0.07 : LABEL_DENSITY;
}

export function getLabelRenderedSizeThreshold(nodeCount: number): number {
  return nodeCount > 2000 ? 8 : LABEL_RENDERED_SIZE_THRESHOLD;
}
