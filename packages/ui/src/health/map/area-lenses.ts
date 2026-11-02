/**
 * Lenses that read the area rollup rather than the file rows: who leads each
 * area, and how much of it only one person knows.
 *
 * Both are area-only. Their files take the colour of the area they sit in, so
 * zooming in keeps the reading instead of turning the field grey.
 */

import { SERIES, SERIES_TAIL } from "../../shared/series";
import type { AreaIndex, MapArea } from "./areas";
import { NEUTRAL_FILL, QUIET_FILL, type LegendRow, type MapLens } from "./lens";
import { NODE_RAMP_STOP } from "../tokens";

/** A share of files mapped onto the three problem stops; quiet below the first. */
export function shareFill(share: number | null, steps: readonly [number, number, number]): string {
  if (share == null) return NEUTRAL_FILL;
  if (share >= steps[2]) return NODE_RAMP_STOP.alert;
  if (share >= steps[1]) return NODE_RAMP_STOP.serious;
  if (share >= steps[0]) return NODE_RAMP_STOP.warning;
  return QUIET_FILL;
}

const pct = (share: number) => `${Math.round(share * 100)}%`;

function share(count: number | undefined, a: MapArea): number | null {
  return count == null || a.fileCount <= 0 ? null : count / a.fileCount;
}

/** Bus-factor-1 share at which an area reads as a knowledge risk. */
const KNOWLEDGE_STEPS = [0.25, 0.5, 0.75] as const;

export const KNOWLEDGE_LENS: MapLens = {
  id: "knowledge",
  label: "Knowledge risk",
  caption: "colour = share of the area's files only one person has changed",
  areaFill: (a) => shareFill(share(a.busFactor1Count, a), KNOWLEDGE_STEPS),
  areaText: (a) => {
    const s = share(a.busFactor1Count, a);
    return s == null ? null : pct(s);
  },
  legend: [
    { fill: NODE_RAMP_STOP.alert, label: "75% or more known by one person" },
    { fill: NODE_RAMP_STOP.serious, label: "50 to 75%" },
    { fill: NODE_RAMP_STOP.warning, label: "25 to 50%" },
    { fill: QUIET_FILL, label: "Under 25%" },
    { fill: NEUTRAL_FILL, label: "No history" },
  ],
  available: ({ areas }) => [...areas.areas.values()].some((a) => a.busFactor1Count != null),
};

/**
 * Owner colours: the series palette past its first step, which is the accent
 * orange and already means "selected" on this map. Everyone past the palette
 * shares the tail grey and is named "Others" in the key.
 */
const OWNER_FILLS = SERIES.slice(1);

/** Levels the owner ranking reads: the first response always carries them. */
const RANK_DEPTH = 2;

/**
 * The people leading the most files, most first, ranked over the top levels
 * only, so a colour does not move to someone else as deeper areas load.
 */
export function rankOwners(index: AreaIndex): { key: string; name: string }[] {
  const weight = new Map<string, { name: string; files: number }>();
  for (const a of index.areas.values()) {
    const o = a.primaryOwner;
    if (!o || (a.path ? a.path.split("/").length : 0) > RANK_DEPTH) continue;
    const w = weight.get(o.key) ?? { name: o.name, files: 0 };
    w.files += a.fileCount;
    weight.set(o.key, w);
  }
  return [...weight.entries()]
    .sort((x, y) => y[1].files - x[1].files || (x[0] < y[0] ? -1 : 1))
    .map(([key, w]) => ({ key, name: w.name }));
}

/** The main-author lens over the areas known so far. */
export function ownerLens(index: AreaIndex): MapLens {
  const ranked = rankOwners(index);
  const colour = new Map(ranked.slice(0, OWNER_FILLS.length).map((o, i) => [o.key, OWNER_FILLS[i]!]));
  const legend: LegendRow[] = ranked
    .slice(0, OWNER_FILLS.length)
    .map((o) => ({ fill: colour.get(o.key)!, label: o.name }));
  if (ranked.length > OWNER_FILLS.length) legend.push({ fill: SERIES_TAIL, label: "Others" });
  legend.push({ fill: NEUTRAL_FILL, label: "No owner on record" });
  return {
    id: "owner",
    label: "Main author",
    caption: "colour = the person who is primary owner of the most files in the area",
    areaFill: (a) => {
      if (a.primaryOwner === undefined) return null;
      if (a.primaryOwner === null) return NEUTRAL_FILL;
      return colour.get(a.primaryOwner.key) ?? SERIES_TAIL;
    },
    areaText: (a) =>
      a.primaryOwner ? `${a.primaryOwner.name.split(" ")[0]} ${pct(a.primaryOwner.share)}` : null,
    legend,
    available: ({ areas }) => [...areas.areas.values()].some((a) => a.primaryOwner !== undefined),
  };
}
