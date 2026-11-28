/**
 * The built-in lenses, by id, and the rules for which a switcher offers.
 *
 * Every entry is a {@link MapLens}. A surface with a lens of its own does not
 * add it here: it builds a `MapLens` and passes it to the map's `lens` prop
 * (see `lens.ts`), so this table stays the Code Health page's vocabulary.
 */

import { healthBandMapFill, NODE_RAMP_STOP } from "../tokens";
import { formatScore } from "@repowise-dev/types/health";
import { EMPTY_AREA_INDEX, type AreaIndex } from "./areas";
import { KNOWLEDGE_LENS, ownerLens, shareFill } from "./area-lenses";
import {
  BAND_LEGEND,
  BURDEN_FILL,
  CLEAR_NEUTRAL_FILL,
  HEALTH_UNSUPPORTED_LABEL,
  NEUTRAL_FILL,
  QUIET_FILL,
  churnFill,
  coverageFill,
  performanceBurden,
  performanceFill,
  scoreFill,
  type MapLens,
} from "./lens";
import type { CodeHealthMapFile, CodeHealthOverlay } from "./types";
import { DEFAULT_MAP_CANVAS_LABELS, type MapCanvasLabels } from "./canvas-labels";

/** NLOC-weighted mean of a per-file score, over the files that carry one. */
function weightedMean(
  files: readonly CodeHealthMapFile[],
  pick: (f: CodeHealthMapFile) => number | null | undefined,
): number | null {
  let w = 0;
  let sum = 0;
  for (const f of files) {
    const v = pick(f);
    if (v == null || f.nloc <= 0) continue;
    w += f.nloc;
    sum += v * f.nloc;
  }
  return w > 0 ? sum / w : null;
}

/**
 * Share of a folder's analysed files that carry something to fix, banded.
 * A share, not a count: a count grows with the folder, so every big folder
 * would read as worst. No green under these lenses, since an absence of
 * findings is not a measurement of good: zero is the quiet neutral.
 */
const FLAGGED_STEPS = [0.1, 0.25] as const;
function flaggedShareFill(
  files: readonly CodeHealthMapFile[],
  flagged: (f: CodeHealthMapFile) => boolean,
  clear: string,
): string {
  const share = files.filter(flagged).length / files.length;
  if (share <= 0) return clear;
  if (share >= FLAGGED_STEPS[1]) return NODE_RAMP_STOP.alert;
  if (share >= FLAGGED_STEPS[0]) return NODE_RAMP_STOP.serious;
  return NODE_RAMP_STOP.warning;
}
/**
 * A lens that folds folders by the share of analysed files flagged. `clear`
 * is the analysed-and-nothing-found tone, distinct from not analysed.
 */
function flaggedLens(flagged: (f: CodeHealthMapFile) => boolean, clear: string) {
  return {
    flagged,
    areaFromFiles: (files: readonly CodeHealthMapFile[]) => flaggedShareFill(files, flagged, clear),
  };
}
const FOLDER_SHARE_LEGEND = (clear: string, clearLabel: string) => [
  { group: "Folders", fill: NODE_RAMP_STOP.alert, label: "25% or more of analysed files flagged" },
  { group: "Folders", fill: NODE_RAMP_STOP.serious, label: "10 to 25%" },
  { group: "Folders", fill: NODE_RAMP_STOP.warning, label: "Under 10%" },
  { group: "Folders", fill: clear, label: clearLabel },
];

/** A file a performance detector looked at: it has a count, and its language is covered. */
const performanceAnalysed = (f: CodeHealthMapFile) => {
  const state = performanceBurden(f).state;
  return state !== "unsupported" && state !== "unknown";
};
const hasCause = (f: CodeHealthMapFile) => performanceBurden(f).count > 0;

const pct = (v: number) => `${Math.round(v)}%`;
const isHotspot = (f: CodeHealthMapFile) => f.is_hotspot === true;

/** Hotspot share at which an area reads as churning. */
const HOTSPOT_STEPS = [0.1, 0.25, 0.5] as const;

/**
 * Hotspot files in their health band, every other file grey, so red and
 * amber appear only where heavy change meets weak health and a healthy
 * hotspot stays green. `chip` words a folder's figure, for translation.
 */
export function hotspotLens(chip: MapCanvasLabels["hotspotChip"]): FileLens {
  return {
    id: "hotspot",
    label: "Hotspot health",
    caption:
      "colour = health of files that change the most · grey = not a hotspot · folder = hotspot count and their health",
    // Only where heavy change meets weak health does the field turn amber or
    // red; a healthy hotspot stays green and every other file goes quiet.
    fill: (f) => (f.is_hotspot ? scoreFill(f.score) : NEUTRAL_FILL),
    analysed: (f) => f.score != null,
    areaFromFiles: (files) => scoreFill(weightedMean(files.filter(isHotspot), (f) => f.score)),
    areaText: (a, files) => {
      const hot = files?.filter(isHotspot) ?? [];
      const count = a.hotspotCount ?? hot.length;
      if (count === 0) return null;
      const health = weightedMean(hot, (f) => f.score);
      return chip(count, health == null ? null : formatScore(health));
    },
    legend: [...BAND_LEGEND, { fill: NEUTRAL_FILL, label: "Grey = not a hotspot" }],
    available: ({ files, areas }) =>
      files.some((f) => f.is_hotspot != null) || [...areas.areas.values()].some((x) => x.hotspotCount !== undefined),
  };
}

/** A lens that reads the file rows, so its `fill` is always there. */
type FileLens = MapLens & { fill: NonNullable<MapLens["fill"]> };
type AreaLensId = "owner" | "knowledge";

export const OVERLAY_SPECS: Record<Exclude<CodeHealthOverlay, AreaLensId>, FileLens> &
  Record<AreaLensId, MapLens> = {
  health: {
    id: "health",
    label: "Code health",
    caption: "disc = folder · size = lines of code · grey = not analysed",
    fill: (f) => scoreFill(f.score),
    analysed: (f) => f.score != null,
    areaFill: (a) => (a.band ? healthBandMapFill(a.band) : NEUTRAL_FILL),
    areaText: (a) => (a.health == null ? null : formatScore(a.health)),
    legend: [...BAND_LEGEND, { fill: NEUTRAL_FILL, label: HEALTH_UNSUPPORTED_LABEL }],
  },
  hotspot: hotspotLens(DEFAULT_MAP_CANVAS_LABELS.hotspotChip),
  maintainability: {
    id: "maintainability",
    label: "Maintainability",
    caption: "color = maintainability score · folders show their loaded files · grey = not measured",
    fill: (f) => scoreFill(f.maintainability_score),
    analysed: (f) => f.maintainability_score != null,
    areaFromFiles: (files) => scoreFill(weightedMean(files, (f) => f.maintainability_score)),
    legend: [...BAND_LEGEND, { fill: NEUTRAL_FILL, label: "not measured" }],
  },
  performance: {
    id: "performance",
    label: "Performance",
    caption:
      "file = open causes · folder = share of its analysed files with one · never a runtime measurement · no green: nothing here is proven fast",
    fill: performanceFill,
    analysed: performanceAnalysed,
    ...flaggedLens(hasCause, CLEAR_NEUTRAL_FILL),
    legend: [
      { group: "Open causes", fill: BURDEN_FILL[3], label: "5 or more" },
      { group: "Open causes", fill: BURDEN_FILL[2], label: "2 to 4" },
      { group: "Open causes", fill: BURDEN_FILL[1], label: "1" },
      { group: "No open cause", fill: NEUTRAL_FILL, label: "Nothing surfaced or not analysed" },
      ...FOLDER_SHARE_LEGEND(CLEAR_NEUTRAL_FILL, "No issues found"),
    ],
  },
  coverage: {
    id: "coverage",
    label: "Coverage",
    caption: "color = line coverage · grey = no coverage data",
    fill: (f) => coverageFill(f.line_coverage_pct),
    analysed: (f) => f.line_coverage_pct != null,
    areaFill: (a) => (a.coveragePct === undefined ? null : coverageFill(a.coveragePct)),
    areaFromFiles: (files) => coverageFill(weightedMean(files, (f) => f.line_coverage_pct)),
    areaText: (a) => (a.coveragePct == null ? null : pct(a.coveragePct)),
    legend: [
      { fill: NODE_RAMP_STOP.alert, label: "<20%" },
      { fill: NODE_RAMP_STOP.serious, label: "20-50%" },
      { fill: NODE_RAMP_STOP.warning, label: "50-80%" },
      { fill: QUIET_FILL, label: "≥80%" },
      { fill: NEUTRAL_FILL, label: "no data" },
    ],
  },
  churn: {
    id: "churn",
    label: "Churn",
    caption: "file = 90-day churn percentile · folder = share of its files that are hotspots",
    fill: (f) => churnFill(f.churn_percentile),
    analysed: (f) => f.churn_percentile != null,
    areaFill: (a) =>
      a.hotspotCount === undefined || a.fileCount <= 0
        ? null
        : shareFill(a.hotspotCount / a.fileCount, HOTSPOT_STEPS),
    areaFromFiles: (files) => churnFill(weightedMean(files, (f) => f.churn_percentile)),
    areaText: (a) =>
      a.hotspotCount === undefined || a.fileCount <= 0 ? null : pct((a.hotspotCount / a.fileCount) * 100),
    legend: [
      { fill: NODE_RAMP_STOP.alert, label: "Top 10%" },
      { fill: NODE_RAMP_STOP.serious, label: "Top 30%" },
      { fill: NODE_RAMP_STOP.warning, label: "Top 60%" },
      { fill: QUIET_FILL, label: "Quiet" },
      { fill: NEUTRAL_FILL, label: "no data" },
    ],
  },
  "dead-code": {
    id: "dead-code",
    label: "Dead code",
    caption: "red = reclaimable lines · grey = clean",
    fill: (f) => ((f.dead_code_lines ?? 0) > 0 ? NODE_RAMP_STOP.alert : NEUTRAL_FILL),
    analysed: (f) => f.dead_code_lines != null,
    ...flaggedLens((f) => (f.dead_code_lines ?? 0) > 0, QUIET_FILL),
    legend: [
      { fill: NODE_RAMP_STOP.alert, label: "Has dead code" },
      { fill: NEUTRAL_FILL, label: "Clean or not analysed" },
      ...FOLDER_SHARE_LEGEND(QUIET_FILL, "No issues found"),
    ],
    available: ({ files }) => files.some((f) => f.dead_code_lines != null),
  },
  security: {
    id: "security",
    label: "Security",
    caption: "red = open findings · grey = clean",
    fill: (f) => ((f.security_findings ?? 0) > 0 ? NODE_RAMP_STOP.alert : NEUTRAL_FILL),
    analysed: (f) => f.security_findings != null,
    ...flaggedLens((f) => (f.security_findings ?? 0) > 0, QUIET_FILL),
    legend: [
      { fill: NODE_RAMP_STOP.alert, label: "Has findings" },
      { fill: NEUTRAL_FILL, label: "Clean or not analysed" },
      ...FOLDER_SHARE_LEGEND(QUIET_FILL, "No issues found"),
    ],
    available: ({ files }) => files.some((f) => f.security_findings != null),
  },
  owner: ownerLens(EMPTY_AREA_INDEX),
  knowledge: KNOWLEDGE_LENS,
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
export const OVERLAY_ORDER: CodeHealthOverlay[] = ["health", "maintainability", "performance"];

/** A built-in lens, built over the areas on hand when its key depends on them. */
export function resolveLens(
  id: CodeHealthOverlay,
  areas: AreaIndex,
  labels?: Pick<MapCanvasLabels, "hotspotChip">,
): MapLens {
  if (id === "owner") return ownerLens(areas);
  if (id === "hotspot" && labels) return hotspotLens(labels.hotspotChip);
  return OVERLAY_SPECS[id] ?? OVERLAY_SPECS.health;
}

/**
 * Whether the data on hand backs a lens. A lens with no data draws every node
 * grey, which reads as "clean" rather than as "not here", so it is not offered.
 */
export function lensAvailable(
  lens: CodeHealthOverlay,
  files: readonly CodeHealthMapFile[],
  areas: AreaIndex = EMPTY_AREA_INDEX,
): boolean {
  const available = OVERLAY_SPECS[lens]?.available;
  return available ? available({ files, areas }) : true;
}
