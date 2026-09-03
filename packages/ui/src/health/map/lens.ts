/**
 * What each lens does to a node, as pure functions over one row.
 *
 * Nothing here reads layout, state, or the DOM, so a legend and the canvas
 * cannot drift: both call the same spec.
 */

import { scoreBand, type ScoreBand } from "../tokens";
import type { CodeHealthMapFile, CodeHealthOverlay, PerformanceActionability } from "./types";

/**
 * Band -> SVG fill var().
 *
 * The canvas ramp, not the semantic ink the score pills use. Those are tuned
 * to be read as small coloured type against the page; this field is thousands
 * of overlapping filled discs, which is a different job in both themes. The
 * two ramps are the same four severity steps in the same hue family and are
 * deliberately not the same values. See `--color-node-*` in globals.css.
 */
const BAND_FILL: Record<ScoreBand, string> = {
  critical: "var(--color-node-critical)",
  poor: "var(--color-node-poor)",
  fair: "var(--color-node-fair)",
  good: "var(--color-node-good)",
};

const BAND_LABEL: { band: ScoreBand; label: string }[] = [
  { band: "critical", label: "Alert" },
  { band: "poor", label: "Warning" },
  { band: "fair", label: "Fair" },
  { band: "good", label: "Healthy" },
];

/**
 * Neutral fill for nodes a lens has no signal for.
 *
 * On a field of thousands this is most of what a reader sees, so it has to sit
 * close enough to the background to read as ground. It is a token rather than
 * a mix of one, because how far toward the page it has to sit is not the same
 * against near-black as against cream.
 */
export const NEUTRAL_FILL = "var(--color-node-neutral)";

export interface LegendRow {
  fill: string;
  label: string;
  /**
   * Which channel this row belongs to. The key prints the group once, where it
   * changes, so a column of swatches reads as the axes it describes rather
   * than as one flat list.
   */
  group?: string;
}

/** Lens metadata: how to fill a node and what the key reads. */
export interface OverlaySpec {
  label: string;
  /** Caption shown under the legend key. */
  caption: string;
  fill: (f: CodeHealthMapFile) => string;
  legend: LegendRow[];
}

/** Score band: the health ramp, quiet grey when the pillar is unscored. */
function scoreFill(score: number | null | undefined): string {
  if (score == null) return NEUTRAL_FILL;
  return BAND_FILL[scoreBand(score)];
}

/** Coverage band: green = well covered, red = uncovered, grey = no data. */
function coverageFill(pct: number | null | undefined): string {
  if (pct == null) return NEUTRAL_FILL;
  if (pct >= 80) return "var(--color-success)";
  if (pct >= 50) return "var(--color-caution)";
  if (pct >= 20) return "var(--color-warning)";
  return "var(--color-error)";
}

/** Churn band: red = top-decile churn, green = quiet. */
function churnFill(pctile: number | null | undefined): string {
  if (pctile == null) return NEUTRAL_FILL;
  if (pctile >= 90) return "var(--color-error)";
  if (pctile >= 70) return "var(--color-warning)";
  if (pctile >= 40) return "var(--color-caution)";
  return "var(--color-success)";
}

/* ------------------------------------------------------------------ *
 * Performance lens
 *
 * The health ramp with its top step removed.
 *
 * This lens is a sibling of the health lens, not a different chart, so it
 * paints with the same four-band ramp, at the same flat opacity, over the same
 * geometry. It uses three of the four bands. A file a detector cleared is a
 * file with no supported pattern in it, not a file measured to be fast, and on
 * this map green means healthy; so performance has no green, and that single
 * rule is the whole difference between the two lenses.
 *
 * An earlier cut said the same thing with its own palette and its own opacity
 * ramp: bands mixed toward the page, quiet files at a fraction of an alpha,
 * and no separating stroke under them. Three channels carrying one variable
 * produced tones that exist nowhere else in the product, went muddy against a
 * dark root, and dissolved the arrangement into a haze in which a ring around
 * one node read as a lasso thrown over its neighbours.
 *
 * The node's colour is the lens's only channel. Actionability - advisory,
 * investigate, or a cause with a stored plan - is named in words by the hover
 * card, the inspector and the ranked list. It had a mark of its own on the
 * field, and on a body of thousands of small packed discs no second mark reads
 * as belonging to one file rather than to the cluster around it.
 * ------------------------------------------------------------------ */

export type PerformanceNodeState =
  | "actionable"
  | "advisory"
  | "investigate"
  | "clear"
  | "unsupported"
  | "unknown";

/** Which unit the burden is counted in, so the copy can say so. */
export type PerformanceBurdenUnit = "opportunities" | "observations";

export interface PerformanceBurden {
  state: PerformanceNodeState;
  count: number;
  unit: PerformanceBurdenUnit;
}

const ACTIONABILITY_STATE: Record<PerformanceActionability, PerformanceNodeState> = {
  plan_ready: "actionable",
  advisory: "advisory",
  investigate: "investigate",
};

/**
 * Read one row's performance state and the size of its burden.
 *
 * A host serving the causal read model gets opportunities. A host that only
 * has raw observations degrades to counting those and says which it counted,
 * rather than reporting a number under the other one's name.
 */
export function performanceBurden(f: CodeHealthMapFile): PerformanceBurden {
  const opportunities = f.performance_opportunities;
  if (opportunities != null) {
    if (opportunities > 0) {
      const state = f.performance_actionability
        ? ACTIONABILITY_STATE[f.performance_actionability]
        : "investigate";
      return { state, count: opportunities, unit: "opportunities" };
    }
    return {
      state: f.performance_analyzed === false ? "unsupported" : "clear",
      count: 0,
      unit: "opportunities",
    };
  }
  const observations = f.performance_findings;
  if (observations != null) {
    if (observations > 0) {
      return { state: "investigate", count: observations, unit: "observations" };
    }
    return {
      state: f.performance_analyzed === false ? "unsupported" : "clear",
      count: 0,
      unit: "observations",
    };
  }
  return {
    state: f.performance_analyzed === false ? "unsupported" : "unknown",
    count: 0,
    unit: "opportunities",
  };
}

/**
 * Fill: how many open causes, on the health ramp, or the quiet neutral.
 *
 * Files with no open cause all take one fill. Whether a detector cleared the
 * file or has no support for its language is a real distinction, but it is one
 * no reader can decode from a second shade of grey, so it is carried by
 * {@link performanceSentence} instead. Coverage and dead code already collapse
 * their own version of it the same way.
 */
export function performanceFill(f: CodeHealthMapFile): string {
  const band = burdenBand(performanceBurden(f).count);
  return band === 0 ? NEUTRAL_FILL : BURDEN_FILL[band];
}

/** Burden band, 0 when the file carries none. Bounded so 40 causes is a step. */
export function burdenBand(count: number): 0 | 1 | 2 | 3 {
  if (count <= 0) return 0;
  if (count === 1) return 1;
  if (count < 5) return 2;
  return 3;
}

/**
 * The three steps, borrowed whole from the health ramp.
 *
 * Same tokens the score pills and the health lens use, so a colour means the
 * same severity wherever it appears on this surface. `BAND_FILL.good` is
 * deliberately absent: it is the only band that would claim a file is fine.
 */
const BURDEN_FILL: Record<1 | 2 | 3, string> = {
  1: BAND_FILL.fair,
  2: BAND_FILL.poor,
  3: BAND_FILL.critical,
};

export const PERFORMANCE_STATE_LABEL: Record<PerformanceNodeState, string> = {
  actionable: "Stored plan",
  advisory: "Advisory",
  investigate: "Needs investigation",
  clear: "Analyzed, nothing surfaced",
  unsupported: "No detector for this language",
  unknown: "Not analyzed here",
};

/** One sentence a hover card or inspector can print about a file. */
export function performanceSentence(f: CodeHealthMapFile): string {
  const { state, count, unit } = performanceBurden(f);
  if (count > 0) {
    const noun = count === 1 ? unit.replace(/s$/, "") : unit;
    return `${count} open ${noun} · ${PERFORMANCE_STATE_LABEL[state].toLowerCase()}`;
  }
  return PERFORMANCE_STATE_LABEL[state];
}

export const OVERLAY_SPECS: Record<CodeHealthOverlay, OverlaySpec> = {
  health: {
    label: "Health",
    caption: "galaxy = module · size = lines of code",
    fill: (f) => BAND_FILL[scoreBand(f.score)],
    legend: BAND_LABEL.map((b) => ({ fill: BAND_FILL[b.band], label: b.label })),
  },
  maintainability: {
    label: "Maintainability",
    caption: "color = maintainability score · grey = not measured",
    fill: (f) => scoreFill(f.maintainability_score),
    legend: [
      ...BAND_LABEL.map((b) => ({ fill: BAND_FILL[b.band], label: b.label })),
      { fill: NEUTRAL_FILL, label: "not measured" },
    ],
  },
  performance: {
    label: "Performance",
    caption: "colour = open causes, never a runtime measurement · no green: nothing here is proven fast",
    fill: performanceFill,
    legend: [
      { group: "Open causes", fill: BURDEN_FILL[3], label: "5 or more" },
      { group: "Open causes", fill: BURDEN_FILL[2], label: "2 to 4" },
      { group: "Open causes", fill: BURDEN_FILL[1], label: "1" },
      { group: "No open cause", fill: NEUTRAL_FILL, label: "Nothing surfaced" },
    ],
  },
  coverage: {
    label: "Coverage",
    caption: "color = line coverage · grey = no coverage data",
    fill: (f) => coverageFill(f.line_coverage_pct),
    legend: [
      { fill: "var(--color-success)", label: "≥80%" },
      { fill: "var(--color-caution)", label: "50-80%" },
      { fill: "var(--color-warning)", label: "20-50%" },
      { fill: "var(--color-error)", label: "<20%" },
      { fill: NEUTRAL_FILL, label: "no data" },
    ],
  },
  churn: {
    label: "Churn",
    caption: "color = 90-day churn percentile",
    fill: (f) => churnFill(f.churn_percentile),
    legend: [
      { fill: "var(--color-error)", label: "Top 10%" },
      { fill: "var(--color-warning)", label: "Top 30%" },
      { fill: "var(--color-caution)", label: "Top 60%" },
      { fill: "var(--color-success)", label: "Quiet" },
      { fill: NEUTRAL_FILL, label: "no data" },
    ],
  },
  "dead-code": {
    label: "Dead code",
    caption: "red = reclaimable lines · grey = clean",
    fill: (f) => ((f.dead_code_lines ?? 0) > 0 ? "var(--color-error)" : NEUTRAL_FILL),
    legend: [
      { fill: "var(--color-error)", label: "Has dead code" },
      { fill: NEUTRAL_FILL, label: "Clean" },
    ],
  },
  security: {
    label: "Security",
    caption: "red = open findings · grey = clean",
    fill: (f) => ((f.security_findings ?? 0) > 0 ? "var(--color-error)" : NEUTRAL_FILL),
    legend: [
      { fill: "var(--color-error)", label: "Has findings" },
      { fill: NEUTRAL_FILL, label: "Clean" },
    ],
  },
};

/**
 * Default lenses offered in the switcher: the three co-equal health signals,
 * all backed by a per-file signal that rides on the map payload itself.
 *
 * Churn is deliberately not in the default. It colors by `churn_percentile`,
 * which arrives on a separate request the host has to join in, so offering it
 * where nobody joined it paints an all-neutral field that reads as "no churn"
 * rather than "no data". Hosts that do the join pass their own `lenses`.
 */
export const OVERLAY_ORDER: CodeHealthOverlay[] = [
  "health",
  "maintainability",
  "performance",
];
