/**
 * What each lens does to a node, as pure functions over one row.
 *
 * Nothing here reads layout, state, or the DOM, so a legend and the canvas
 * cannot drift: both call the same spec.
 */

import {
  HEALTH_BAND_LABEL,
  HEALTH_BAND_ORDER,
  HEALTH_BAND_RANGE_LABEL,
  formatScore,
} from "@repowise-dev/types/health";
import { NODE_HEALTHY_FILL, NODE_RAMP_STOP, healthBandMapFill, healthRampFill } from "../tokens";
import type { AreaIndex, MapArea } from "./areas";
import type { CodeHealthMapFile, PerformanceActionability } from "./types";

/** The one quiet tone every file that needs nothing takes, under every lens. */
export const QUIET_FILL = NODE_HEALTHY_FILL;

/**
 * Neutral fill for nodes a lens has no signal for.
 *
 * On a field of thousands this is most of what a reader sees, so it has to sit
 * close enough to the background to read as ground. It is a token rather than
 * a mix of one, because how far toward the page it has to sit is not the same
 * against near-black as against cream.
 */
export const NEUTRAL_FILL = "var(--color-node-neutral)";

/**
 * A folder a lens checked and found nothing in, where green would overclaim
 * (performance: no supported pattern is not proven fast). The neutral pulled
 * toward the page, so "checked, clear" never reads as "not checked".
 */
export const CLEAR_NEUTRAL_FILL = "color-mix(in srgb, var(--color-node-neutral) 40%, var(--color-bg-surface))";

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

/** Worst-first legend rows on the five canonical bands, as every score pill reads. */
export const BAND_LEGEND: LegendRow[] = HEALTH_BAND_ORDER.map((band) => ({
  fill: healthBandMapFill(band),
  label: `${HEALTH_BAND_LABEL[band]} · ${HEALTH_BAND_RANGE_LABEL[band]}`,
}));

/**
 * A lens: how the map colours and names what it draws, and what its key reads.
 *
 * This is the extension point other surfaces plug into without touching the
 * canvas. A lens is plain data plus pure functions, so a host can build one
 * over data only it holds (an off-boarding scenario, a coupling read) by
 * closing over that data, and hand it to `CodeHealthMap`'s `lens` prop and to
 * `MapLegend`. Every method is optional past the key: a lens that only knows
 * files leaves `areaFill` out and areas fold their loaded files through
 * `areaFromFiles`; a lens that only knows areas leaves `fill` out and each
 * file takes the colour of the area it sits in.
 *
 * Colours are CSS strings (token `var()`s or `color-mix()`es), never raw hex.
 */
export interface MapLens {
  /** Stable id, for a switcher and the URL. */
  id: string;
  label: string;
  /** Caption shown under the legend key. */
  caption: string;
  legend: LegendRow[];
  /** A file's fill. Absent: the file takes its area's fill. */
  fill?: (f: CodeHealthMapFile) => string;
  /** An area's fill from its rollup; `null` when the rollup lacks the signal. */
  areaFill?: (a: MapArea) => string | null;
  /**
   * Whether a file carries this lens's signal at all. A file that does not is
   * not analysed: it is drawn grey and left out of every folder's fold, never
   * read as the worst value. Absent: every file carries it.
   */
  analysed?: (f: CodeHealthMapFile) => boolean;
  /** Whether an analysed file carries something to fix, for "N of M flagged". */
  flagged?: (f: CodeHealthMapFile) => boolean;
  /**
   * An area's fill from its loaded, analysed files, when `areaFill` cannot
   * say. Never called with an empty list: a folder with no analysed file is
   * grey. Fold by share, not by count, or every big folder reads as worst.
   */
  areaFromFiles?: (files: readonly CodeHealthMapFile[]) => string;
  /**
   * The short figure printed on an area's disc ("7.4", "62%"). `files` are
   * the area's loaded files this lens analysed, when any are loaded.
   */
  areaText?: (a: MapArea, files?: readonly CodeHealthMapFile[]) => string | null;
  /** Whether the data on hand backs this lens; an unbacked lens is not offered. */
  available?: (ctx: LensContext) => boolean;
}

/** What a lens's availability is judged against. */
export interface LensContext {
  files: readonly CodeHealthMapFile[];
  areas: AreaIndex;
}

/** @deprecated The built-in lenses' old name; every lens is a {@link MapLens}. */
export type OverlaySpec = MapLens;

/** Score: the map ramp, the neutral when the pillar is unscored. */
export function scoreFill(score: number | null | undefined): string {
  if (score == null) return NEUTRAL_FILL;
  return healthRampFill(score);
}

/** Coverage band: quiet when well covered, the ramp's stops below that. */
export function coverageFill(pct: number | null | undefined): string {
  if (pct == null) return NEUTRAL_FILL;
  if (pct >= 80) return QUIET_FILL;
  if (pct >= 50) return NODE_RAMP_STOP.warning;
  if (pct >= 20) return NODE_RAMP_STOP.serious;
  return NODE_RAMP_STOP.alert;
}

/** Churn band: quiet below the top 60%, the ramp's stops above it. */
export function churnFill(pctile: number | null | undefined): string {
  if (pctile == null) return NEUTRAL_FILL;
  if (pctile >= 90) return NODE_RAMP_STOP.alert;
  if (pctile >= 70) return NODE_RAMP_STOP.serious;
  if (pctile >= 40) return NODE_RAMP_STOP.warning;
  return QUIET_FILL;
}

/* ------------------------------------------------------------------ *
 * Performance lens
 *
 * The health ramp with its top step removed.
 *
 * This lens is a sibling of the health lens, not a different chart, so it
 * paints with the same band ramp, at the same flat opacity, over the same
 * geometry. It uses only the bands below green. A file a detector cleared is a
 * file with no supported pattern in it, not a file measured to be fast, and on
 * this map green means the code is fine; so performance has no green, and that
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
  // Nothing to change; the server already leaves these out of the count.
  expected: "clear",
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
 * The three steps, borrowed from the map ramp's stops.
 *
 * The same values the health lens paints at 7, 5 and 2.5, so a colour means
 * the same severity wherever it appears on this field. The quiet tone is
 * deliberately absent: it is the one that would claim a file is fine.
 */
export const BURDEN_FILL: Record<1 | 2 | 3, string> = {
  1: NODE_RAMP_STOP.warning,
  2: NODE_RAMP_STOP.serious,
  3: NODE_RAMP_STOP.alert,
};

export const PERFORMANCE_STATE_LABEL: Record<PerformanceNodeState, string> = {
  actionable: "Stored plan",
  advisory: "Advisory",
  investigate: "Needs investigation",
  clear: "Analyzed, nothing surfaced",
  unsupported: "No detector for this language",
  unknown: "Not analyzed here",
};

/**
 * What the health lenses say for a file with no score: the same words the
 * performance lens uses for a file no detector covers, because it is the same
 * situation one pillar over. Grey, never green.
 */
export const HEALTH_UNSUPPORTED_LABEL = PERFORMANCE_STATE_LABEL.unsupported;

/** The one line a map shows when not one drawn file has a score. */
export const HEALTH_UNSUPPORTED_NOTICE =
  "Code health analysis does not support this language yet, so its files are shown grey.";

/** True when the field has files and none of them carries a score. */
export function noFileScored(files: CodeHealthMapFile[]): boolean {
  return files.length > 0 && files.every((f) => f.score == null);
}

/** A file's score at one decimal, or the unsupported label. */
export function scoreText(f: CodeHealthMapFile): string {
  return f.score == null ? HEALTH_UNSUPPORTED_LABEL : formatScore(f.score);
}

/** One sentence a hover card or inspector can print about a file. */
export function performanceSentence(f: CodeHealthMapFile): string {
  const { state, count, unit } = performanceBurden(f);
  if (count > 0) {
    const noun = count === 1 ? unit.replace(/s$/, "") : unit;
    return `${count} open ${noun} · ${PERFORMANCE_STATE_LABEL[state].toLowerCase()}`;
  }
  return PERFORMANCE_STATE_LABEL[state];
}
