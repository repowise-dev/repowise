/**
 * Which areas the rail points at: the ones needing attention, and the one
 * that improved most. Pure, over the areas the index knows.
 *
 * Only drawn discs count (a pass-through directory is its child), and a pick
 * never nests inside another, so three suggestions are three places.
 */

import { ROOT_AREA } from "../../lib/areas";
import type { AreaIndex, MapArea } from "./areas";
import { chainEnd, isWithin } from "./area-paths";

/** An area this small is a detail, not a place to send someone. */
export const MIN_RANKED_FILES = 5;

/** The drawn areas strictly inside `within`. */
function drawnInside(index: AreaIndex, within: string): MapArea[] {
  const out = new Map<string, MapArea>();
  for (const a of index.areas.values()) {
    if (a.path === within || !isWithin(a.path, within)) continue;
    const end = chainEnd(index, a.path);
    const drawn = index.areas.get(end);
    if (drawn && end !== within) out.set(end, drawn);
  }
  return [...out.values()];
}

function pickDisjoint(sorted: MapArea[], n: number): MapArea[] {
  const picked: MapArea[] = [];
  for (const a of sorted) {
    if (picked.length >= n) break;
    if (picked.some((p) => isWithin(a.path, p.path) || isWithin(p.path, a.path))) continue;
    picked.push(a);
  }
  return picked;
}

/** The lowest-scoring places inside `within`, worst first, none nested in another. */
export function areasNeedingAttention(index: AreaIndex, within: string = ROOT_AREA, n = 3): MapArea[] {
  const ranked = drawnInside(index, within)
    .filter((a) => a.health != null && a.fileCount >= MIN_RANKED_FILES)
    .sort((a, b) => a.health! - b.health! || b.nloc - a.nloc || (a.path < b.path ? -1 : 1));
  return pickDisjoint(ranked, n);
}

/** The area inside `within` whose score rose most over the delta window, if any rose. */
export function mostImproved(index: AreaIndex, within: string = ROOT_AREA): MapArea | null {
  const ranked = drawnInside(index, within)
    .filter((a) => (a.delta30d ?? 0) > 0 && a.fileCount >= MIN_RANKED_FILES)
    .sort((a, b) => b.delta30d! - a.delta30d! || (a.path < b.path ? -1 : 1));
  return ranked[0] ?? null;
}
