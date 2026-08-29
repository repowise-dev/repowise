/**
 * What each lens does to a node, as pure functions over one row.
 *
 * Nothing here reads layout, state, or the DOM, so a legend and the canvas
 * cannot drift: both call the same spec.
 */

import { scoreBand, type ScoreBand } from "../tokens";
import type { CodeHealthMapFile, CodeHealthOverlay, PerformanceActionability } from "./types";

/* Band -> SVG fill var(). The same ramp as every score pill on the surface. */
const BAND_FILL: Record<ScoreBand, string> = {
  critical: "var(--color-error)",
  poor: "var(--color-warning)",
  fair: "var(--color-caution)",
  good: "var(--color-success)",
};

const BAND_LABEL: { band: ScoreBand; label: string }[] = [
  { band: "critical", label: "Alert" },
  { band: "poor", label: "Warning" },
  { band: "fair", label: "Fair" },
  { band: "good", label: "Healthy" },
];

/** Neutral fill for nodes a non-health lens has no signal for. */
export const NEUTRAL_FILL = "var(--color-text-tertiary)";

/** Quieter still: the file was never looked at, which is not "no signal". */
export const ABSENT_FILL = "var(--color-border-default)";

export interface LegendRow {
  fill: string;
  label: string;
  /**
   * How the swatch is drawn. `ring` is an outline rather than a disc, so the
   * performance key reads as the mark it describes; `dash` mirrors the ring's
   * stroke pattern, which is the lens's non-colour channel.
   */
  mark?: "dot" | "ring";
  dash?: string;
  /**
   * Which channel this row belongs to. The performance lens marks a node on
   * two independent axes, and a flat list of swatches gives a reader no way to
   * see that the first three are one axis and the next three another. The key
   * prints the group once, where it changes.
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
 * Two channels, neither of them the node's fill.
 *
 * The fill stays quiet and says only whether anything looked at the file,
 * because the pillar is high precision and low recall: a file a detector
 * cleared is a file with no *supported pattern* in it, which is not a
 * measurement that it is fast. Painting that green would put the strongest
 * reassurance on the surface on the weakest evidence it has.
 *
 * The pressure ring carries the burden: its width and colour step with the
 * number of open causes, and its stroke pattern says what a reader could do
 * about them. Both are named in words in the key, the hover card, and the
 * inspector, so neither colour nor pattern is load-bearing on its own.
 * ------------------------------------------------------------------ */

export type PerformanceNodeState =
  | "actionable"
  | "advisory"
  | "investigate"
  | "clear"
  | "unsupported"
  | "unknown";

/** Which unit the ring is counting, so the copy can say so. */
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

/** Fill: analysis state only, and never the healthy green. */
export function performanceFill(f: CodeHealthMapFile): string {
  const { state } = performanceBurden(f);
  return state === "unsupported" || state === "unknown" ? ABSENT_FILL : NEUTRAL_FILL;
}

/** Burden band, 0 when the file carries none. Bounded so 40 causes is a step. */
export function burdenBand(count: number): 0 | 1 | 2 | 3 {
  if (count <= 0) return 0;
  if (count === 1) return 1;
  if (count < 5) return 2;
  return 3;
}

const BAND_STROKE: Record<1 | 2 | 3, string> = {
  1: "var(--color-caution)",
  2: "var(--color-warning)",
  3: "var(--color-error)",
};

const BAND_WIDTH: Record<1 | 2 | 3, number> = { 1: 1, 2: 1.8, 3: 2.6 };

/** Dash pattern per state: solid is a stored plan, dotted is an open question. */
const STATE_DASH: Record<PerformanceNodeState, string | undefined> = {
  actionable: undefined,
  advisory: "3 2",
  investigate: "1 2",
  clear: undefined,
  unsupported: undefined,
  unknown: "1 2",
};

export interface PressureRing {
  stroke: string;
  /** Stroke width in user units, before the caller divides by the zoom scale. */
  width: number;
  dash?: string;
}

/** The ring for one row, or `null` when the file carries no open burden. */
export function pressureRing(f: CodeHealthMapFile): PressureRing | null {
  const { state, count } = performanceBurden(f);
  const band = burdenBand(count);
  if (band === 0) return null;
  const dash = STATE_DASH[state];
  return {
    stroke: BAND_STROKE[band],
    width: BAND_WIDTH[band],
    ...(dash ? { dash } : {}),
  };
}

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
    caption:
      "galaxy = module · dot = file, sized by lines of code · ring = open causes, never a runtime measurement",
    fill: performanceFill,
    legend: [
      { group: "Ring colour, how many", fill: BAND_STROKE[3], label: "5+ causes", mark: "ring" },
      { group: "Ring colour, how many", fill: BAND_STROKE[2], label: "2-4", mark: "ring" },
      { group: "Ring colour, how many", fill: BAND_STROKE[1], label: "1", mark: "ring" },
      {
        group: "Ring pattern, what you can do",
        fill: NEUTRAL_FILL,
        label: "Stored plan",
        mark: "ring",
      },
      {
        group: "Ring pattern, what you can do",
        fill: NEUTRAL_FILL,
        label: "Advisory",
        mark: "ring",
        dash: "3 2",
      },
      {
        group: "Ring pattern, what you can do",
        fill: NEUTRAL_FILL,
        label: "Investigate",
        mark: "ring",
        dash: "1 2",
      },
      { group: "No ring", fill: NEUTRAL_FILL, label: "Analyzed, nothing surfaced" },
      { group: "No ring", fill: ABSENT_FILL, label: "Not analyzed" },
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
