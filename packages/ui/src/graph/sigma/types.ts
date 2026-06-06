/**
 * Sigma-specific attribute types for Graphology graph instances.
 *
 * These extend the base Repowise types (GraphNode, ModuleNode) with
 * rendering attributes that Sigma.js needs: position, size, color,
 * and interaction state.
 */

export interface SigmaNodeAttributes {
  // Position (required by Sigma)
  x: number;
  y: number;

  // Rendering
  size: number;
  color: string;
  label: string;

  // Identity. "hub"/"core" are the constellation (radial) super-graph kinds:
  // one hub disc per community, a single dark repo-core at the origin.
  nodeType: "file" | "module" | "hub" | "core";
  fullPath: string;
  language: string;

  // Graph metrics (from GraphNode)
  communityId: number;
  pagerank: number;
  betweenness: number;
  isTest: boolean;
  isEntryPoint: boolean;
  hasDoc: boolean;
  symbolCount: number;

  // Module-specific (from ModuleNode, only set when nodeType === "module")
  fileCount?: number | undefined;
  avgPagerank?: number | undefined;
  docCoveragePct?: number | undefined;
  dominantCommunityId?: number | undefined;

  // Constellation hub-specific (only set when nodeType === "hub" / "core")
  memberCount?: number | undefined;
  hotspotCount?: number | undefined;
  deadCount?: number | undefined;
  languages?: string[] | undefined;
  /** Family hub hue for the soft halo ring drawn around hub discs. */
  haloColor?: string | undefined;
  /** Render the label even when density culling would hide it (hubs/core). */
  forceLabel?: boolean | undefined;

  // Signal overlays (set by adapter based on signal data)
  isHotspot?: boolean | undefined;
  isDead?: boolean | undefined;
  commitCount?: number | undefined;

  // Cross-link signals from enriched backend payloads
  churnPercentile?: number | null | undefined;
  deadConfidence?: number | null | undefined;
  hasDecision?: boolean | undefined;
  primaryOwner?: string | null | undefined;

  // Interaction state (mutated by reducers at render time)
  hidden?: boolean | undefined;
  zIndex?: number | undefined;
  highlighted?: boolean | undefined;

  // ForceAtlas2 mass parameter (higher = more repulsion)
  mass?: number | undefined;

  // For dimming/restoring original color
  originalColor?: string | undefined;
}

export interface SigmaEdgeAttributes {
  size: number;
  color: string;

  // Sigma edge program type ("curved" for small graphs, "line" for large)
  type: "curved" | "line";

  // Random curvature for visual variety (0.12-0.20)
  curvature: number;

  // Semantic classification of the edge
  edgeKind:
    | "import"
    | "crossCommunity"
    | "internal"
    | "dynamic"
    | "lowConfidence";

  // Original data
  importedNames: string[];
  edgeCount: number;
  confidence?: number | undefined;

  // Interaction state
  hidden?: boolean | undefined;
  zIndex?: number | undefined;
}
