/**
 * Per-parent masonry pack layout. Pure, browser-free and unit-testable.
 *
 * The backend ships a containment tree but its layout rects are a squarified
 * treemap (space-filling, extreme aspect ratios). We ignore those and lay each
 * parent's children out client-side instead: every card is sized by importance
 * (more central = bigger, within bounds so nothing is unreadably small) and the
 * cards are row-packed into centred, variable-width rows. The staggered row
 * edges and the size variation give an organic architecture-map feel rather than
 * a rigid grid, while the whole cluster is uniform-fit (aspect-preserving) into
 * the parent so cards keep a single landscape shape. The rect emitted is still in
 * parent `[0,1]` space, so the clip-and-scale composition in `geometry.ts` is
 * unchanged.
 */

import type { Rect } from "./camera";
import {
  CARD_ASPECT,
  PACK_GUTTER,
  PACK_IMPORTANCE_GAMMA,
  PACK_SIZE_MAX,
  PACK_SIZE_MIN,
} from "./constants";

/** The minimal slice of a node the layout needs (placement order + sizing). */
export interface LayoutChild {
  id: string;
  sibling_rank: number;
  importance: number;
}

export interface PackLayoutOptions {
  /** Whitespace between cards, in the same relative units as the card sides. */
  gutter?: number;
  /** Card height for the least-important sibling. */
  sizeMin?: number;
  /** Card height for the most-important sibling. */
  sizeMax?: number;
  /** Importance curve; <1 lifts small cards so minor nodes stay readable. */
  gamma?: number;
  /** Card width : height. */
  aspect?: number;
}

/**
 * Choose a target column count whose column/row ratio tracks the parent's world
 * aspect, so the packed cluster's bounding box roughly matches the parent shape
 * and fits with little margin. Returns the tightened dimensions (no empty
 * trailing column). Shared with the pack (which packs to ~`cols` cards per row).
 */
export function gridDimensions(
  count: number,
  parentAspect: number,
): { cols: number; rows: number } {
  if (count <= 0) return { cols: 0, rows: 0 };
  if (count === 1) return { cols: 1, rows: 1 };
  const aspect = Math.min(6, Math.max(1 / 6, parentAspect || 1));
  let cols = Math.round(Math.sqrt(count * aspect));
  cols = Math.min(count, Math.max(1, cols));
  const rows = Math.ceil(count / cols);
  // Re-derive columns from the row count so the grid is as tight as possible for
  // that many rows (this can narrow the column count, which is intended).
  return { cols: Math.ceil(count / rows), rows };
}

interface Packed {
  id: string;
  w: number;
  h: number;
}

/**
 * Lay `children` out as an importance-weighted masonry pack inside the unit
 * parent box. Children are ordered by `sibling_rank` (most important first, so
 * the biggest cards read top-left), sized by importance normalised across the
 * sibling set, then greedily row-packed to ~`cols` cards per row. Rows are
 * centred (so left edges stagger) and each card is centred in its row band (so
 * top edges stagger); the whole cluster is uniform-fit into `[0,1]²`. Returns
 * each child's rect in parent `[0,1]` space. Deterministic: stable order, ties
 * broken by id; no randomness.
 */
export function packLayout(
  children: readonly LayoutChild[],
  parentAspect: number,
  opts: PackLayoutOptions = {},
): Map<string, Rect> {
  const gutter = opts.gutter ?? PACK_GUTTER;
  const sizeMin = opts.sizeMin ?? PACK_SIZE_MIN;
  const sizeMax = opts.sizeMax ?? PACK_SIZE_MAX;
  const gamma = opts.gamma ?? PACK_IMPORTANCE_GAMMA;
  const aspect = opts.aspect ?? CARD_ASPECT;

  const out = new Map<string, Rect>();
  const n = children.length;
  if (n === 0) return out;

  const ordered = [...children].sort(
    (a, b) => a.sibling_rank - b.sibling_rank || (a.id < b.id ? -1 : 1),
  );

  // Normalise importance across this sibling set so each parent shows a full
  // range of sizes (min-max within the group). If they are all equal, everyone
  // gets the max size (a uniform, generously-sized cluster).
  let lo = Infinity;
  let hi = -Infinity;
  for (const c of ordered) {
    const v = Number.isFinite(c.importance) ? c.importance : 0;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  const span = hi - lo;
  const heightOf = (c: LayoutChild): number => {
    const v = Number.isFinite(c.importance) ? c.importance : 0;
    const norm = span > 1e-9 ? (v - lo) / span : 1;
    return sizeMin + (sizeMax - sizeMin) * Math.pow(norm, gamma);
  };

  const cards: Packed[] = ordered.map((c) => {
    const h = heightOf(c);
    return { id: c.id, w: h * aspect, h };
  });

  // Greedy row packing to a target width of ~`cols` average-width cards.
  const { cols } = gridDimensions(n, parentAspect);
  const avgW = cards.reduce((s, c) => s + c.w, 0) / n;
  const targetRowW = cols * avgW + Math.max(0, cols - 1) * gutter;

  const rows: Packed[][] = [];
  let cur: Packed[] = [];
  let curW = 0;
  for (const card of cards) {
    const add = (cur.length > 0 ? gutter : 0) + card.w;
    if (cur.length > 0 && curW + add > targetRowW) {
      rows.push(cur);
      cur = [];
      curW = 0;
    }
    curW += (cur.length > 0 ? gutter : 0) + card.w;
    cur.push(card);
  }
  if (cur.length > 0) rows.push(cur);

  const rowW = (row: Packed[]): number =>
    row.reduce((s, c) => s + c.w, 0) + Math.max(0, row.length - 1) * gutter;
  const rowH = (row: Packed[]): number => row.reduce((m, c) => Math.max(m, c.h), 0);

  const maxRowW = Math.max(...rows.map(rowW));
  const totalH = rows.reduce((s, r) => s + rowH(r), 0) + Math.max(0, rows.length - 1) * gutter;

  // Uniform (aspect-preserving) fit of the packed cluster into the unit box,
  // centred, so cards keep one shape and nothing spills outside the parent.
  const scale = Math.min(1 / maxRowW, 1 / totalH);
  const offX = (1 - maxRowW * scale) / 2;
  const offY = (1 - totalH * scale) / 2;

  let y = 0;
  for (const row of rows) {
    const h = rowH(row);
    let x = (maxRowW - rowW(row)) / 2; // centre the row -> staggered left edges
    for (const card of row) {
      const cy = y + (h - card.h) / 2; // centre in the band -> staggered top edges
      out.set(card.id, {
        x: offX + x * scale,
        y: offY + cy * scale,
        w: card.w * scale,
        h: card.h * scale,
      });
      x += card.w + gutter;
    }
    y += h + gutter;
  }
  return out;
}
