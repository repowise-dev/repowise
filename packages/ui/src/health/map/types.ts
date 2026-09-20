/**
 * The map's data shapes, shared by the pure layers behind the facade.
 *
 * Kept apart from the encodings and the layout so neither imports the other:
 * a fill function should not be able to reach the packing algorithm.
 */

export interface CodeHealthMapFile {
  file_path: string;
  score: number;
  nloc: number;
  module: string | null;
  line_coverage_pct: number | null;
  has_test_file: boolean;
  /** Maintainability pillar score (1-10), drives the maintainability lens. */
  maintainability_score?: number | null;
  /** Performance-risk pillar score. Never drawn: it compresses to [8,10], so a
   *  score ramp paints the field one colour and hides where the risk is. */
  performance_score?: number | null;
  /** Open causal opportunities on this file. The performance lens rings by
   *  this, because an opportunity is one thing a reader could go and do. */
  performance_opportunities?: number | null;
  /** Observations behind those opportunities. */
  performance_observations?: number | null;
  /** Best next step available on the file: a stored plan beats an advisory
   *  intervention, which beats an investigation. */
  performance_actionability?: PerformanceActionability | null;
  /** Best queue rank among the file's opportunities, for ranked lists. */
  performance_rank?: number | null;
  /** Open performance observations, from a host that serves no causal read
   *  model. The lens falls back to it and says which unit it is counting. */
  performance_findings?: number | null;
  /** Whether a performance detector runs on this file's language at all.
   *  `false` means nothing ever looked, which is not the same as clear. */
  performance_analyzed?: boolean | null;
  /** 0-100 churn percentile, drives the churn lens. */
  churn_percentile?: number | null;
  /** Reclaimable lines, drives the dead-code lens. */
  dead_code_lines?: number | null;
  /** Open security findings, drives the security lens. */
  security_findings?: number | null;
}

export type PerformanceActionability = "plan_ready" | "advisory" | "investigate";

/**
 * Lens applied to the same field. Recolors every node without re-laying the
 * galaxies, so spatial memory survives a lens change.
 */
export type CodeHealthOverlay =
  | "health"
  | "maintainability"
  | "performance"
  | "coverage"
  | "churn"
  | "dead-code"
  | "security";

/** One module = one galaxy: its files plus size aggregates. */
export interface GalaxyAgg {
  module: string;
  files: CodeHealthMapFile[]; // NLOC-desc
  totalNloc: number;
  maxNloc: number;
}

export interface FileNode {
  file: CodeHealthMapFile;
  x: number;
  y: number;
  r: number;
}

export interface Galaxy {
  module: string;
  cx: number;
  cy: number;
  R: number;
  fileCount: number;
  colorId: number;
  nodes: FileNode[];
}

/**
 * What the server drew and what it left out, as one object.
 *
 * The field and the sentence describing it come from the same response, so a
 * caption can never claim a scope the canvas does not have.
 */
export interface MapScope {
  shown: number;
  /** Files that could be drawn at all. A zero-line file cannot be sized. */
  eligible: number;
  repository: number;
  cap: number;
  omitted: {
    files: number;
    /**
     * Files carrying an open cause that the cap pushed out. `null` when the
     * host cannot count them: zero would claim every cause is on screen, which
     * is the one thing a caption here must never say without knowing it.
     */
    performanceFiles: number | null;
    opportunities: number | null;
    observations: number | null;
  };
  /** Paths the caller pinned that the index holds no drawable row for. */
  missing?: string[];
}
