/**
 * Hit-testing the last drawn frame. Pure, browser-free and unit-testable.
 *
 * The draw pass records every card it paints, deepest last, and a pick walks
 * that list. Entries are pooled per `DrawStats`: each frame rewrites the ones
 * the previous frame used, so a repaint allocates no hit-test entries. An
 * entry is valid until the next draw into the same stats; read what you need
 * from it at once.
 */

import type { Rect } from "./camera";
import type { ExternalLane } from "./externals";

export interface PickEntry {
  id: string;
  rect: Rect;
  depth: number;
  /** Set on an external card: the rail it sits in, so a pick knows whose importers to name. */
  lane?: ExternalLane | undefined;
}

export interface DrawStats {
  drawn: number;
  culled: number;
  maxDepthDrawn: number;
  /** Drawn cards, deepest last, for hit-testing the most recent frame. */
  pick: PickEntry[];
}

/** Entries ever handed out per stats object, reused frame to frame. */
const pools = new WeakMap<DrawStats, PickEntry[]>();

export function createDrawStats(): DrawStats {
  return { drawn: 0, culled: 0, maxDepthDrawn: 0, pick: [] };
}

/** Empty `stats` for a new frame, keeping its entries for reuse. */
export function resetDrawStats(stats: DrawStats): void {
  stats.drawn = 0;
  stats.culled = 0;
  stats.maxDepthDrawn = 0;
  stats.pick.length = 0;
}

/** Record a drawn card for hit-testing; `rect` is copied. */
export function pushPick(
  stats: DrawStats,
  id: string,
  rect: Rect,
  depth: number,
  lane?: ExternalLane,
): void {
  let pool = pools.get(stats);
  if (!pool) pools.set(stats, (pool = []));
  const i = stats.pick.length;
  const entry = pool[i] ?? (pool[i] = { id, rect: { x: 0, y: 0, w: 0, h: 0 }, depth });
  entry.id = id;
  entry.rect.x = rect.x;
  entry.rect.y = rect.y;
  entry.rect.w = rect.w;
  entry.rect.h = rect.h;
  entry.depth = depth;
  entry.lane = lane;
  stats.pick.push(entry);
}

/** Deepest drawn card containing the screen point, or null. Pure. */
export function pickNode(stats: DrawStats, sx: number, sy: number): PickEntry | null {
  let best: PickEntry | null = null;
  for (const entry of stats.pick) {
    const r = entry.rect;
    if (sx < r.x || sx > r.x + r.w || sy < r.y || sy > r.y + r.h) continue;
    if (!best || entry.depth >= best.depth) best = entry;
  }
  return best;
}
