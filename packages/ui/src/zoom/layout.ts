/**
 * Per-parent masonry pack layout. Pure, browser-free and unit-testable.
 *
 * The backend ships a containment tree and no placement. Every card is sized by
 * importance (more central = bigger, within bounds so nothing is unreadably
 * small) and the cards are row-packed into centred, variable-width rows. The
 * staggered row edges and the size variation give an organic architecture-map
 * feel rather than a rigid grid, while the whole cluster is uniform-fit
 * (aspect-preserving) into the parent so cards keep a single landscape shape.
 * The rect emitted is in parent `[0,1]` space, which is what the clip-and-scale
 * composition in `geometry.ts` consumes.
 */

import type { Rect } from "./camera";
import { labelAspect } from "./card-content";
import {
  CARD_ASPECT,
  PACK_GUTTER,
  PACK_IMPORTANCE_GAMMA,
  PACK_SIZE_MAX,
  PACK_SIZE_MIN,
} from "./constants";
import { fileLevelLayout } from "./layout-files";
import type { ZoomKind } from "./types";

/** The minimal slice of a node the layout needs (placement order + sizing). */
export interface LayoutChild {
  id: string;
  sibling_rank: number;
  importance: number;
  /** When given (with `kind`), the card widens so this name fits (see `labelAspect`). */
  name?: string;
  kind?: ZoomKind;
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
  /** Card width : height (a long name may widen a card past it). */
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

/** Column and row counts tried either side of the aspect estimate (see `packLayout`). */
const COL_SEARCH = 2;

interface PackedRows {
  rows: Packed[][];
  maxRowW: number;
  totalH: number;
  /** Uniform scale that fits the cluster in the unit box. */
  scale: number;
}

function rowW(row: readonly Packed[], gutter: number): number {
  return row.reduce((s, c) => s + c.w, 0) + Math.max(0, row.length - 1) * gutter;
}

function rowH(row: readonly Packed[]): number {
  return row.reduce((m, c) => Math.max(m, c.h), 0);
}

/**
 * Greedy rows against a target width, and their fit. `balanced` starts a new
 * row once a card's centre (not its far edge) would pass the target, which
 * evens the rows out when the target is the average row width.
 */
function packRows(
  cards: readonly Packed[],
  targetW: number,
  gutter: number,
  balanced: boolean,
): PackedRows {
  const rows: Packed[][] = [];
  let cur: Packed[] = [];
  let curW = 0;
  for (const card of cards) {
    const reach = balanced ? card.w / 2 : card.w;
    if (cur.length > 0 && curW + gutter + reach > targetW) {
      rows.push(cur);
      cur = [];
      curW = 0;
    }
    curW += (cur.length > 0 ? gutter : 0) + card.w;
    cur.push(card);
  }
  if (cur.length > 0) rows.push(cur);
  let maxRowW = 0;
  let totalH = Math.max(0, rows.length - 1) * gutter;
  for (const row of rows) {
    maxRowW = Math.max(maxRowW, rowW(row, gutter));
    totalH += rowH(row);
  }
  return { rows, maxRowW, totalH, scale: Math.min(1 / maxRowW, 1 / totalH) };
}

/**
 * Lay `children` out as an importance-weighted masonry pack inside the unit
 * parent box. Children are ordered by `sibling_rank` (most important first, so
 * the biggest cards read top-left), sized by importance normalised across the
 * sibling set, widened where a long name needs it, then greedily row-packed to
 * ~`cols` cards per row. Rows are centred (so left edges stagger) and each card
 * is centred in its row band (so top edges stagger); the whole cluster is uniform-fit into `[0,1]²`. Returns
 * each child's rect in parent `[0,1]` space. Deterministic: stable order, ties
 * broken by id; no randomness.
 */
export function packLayout(
  children: readonly LayoutChild[],
  parentAspect: number,
  opts: PackLayoutOptions = {},
): Map<string, Rect> {
  // A parent of mostly files gets grouped tiles instead (see `layout-files.ts`).
  const tiles = fileLevelLayout(children, parentAspect);
  if (tiles) return tiles;
  const gutter = opts.gutter ?? PACK_GUTTER;
  const sizeMin = opts.sizeMin ?? PACK_SIZE_MIN;
  const sizeMax = opts.sizeMax ?? PACK_SIZE_MAX;
  const gamma = opts.gamma ?? PACK_IMPORTANCE_GAMMA;
  const baseAspect = opts.aspect ?? CARD_ASPECT;
  // Cards are sized in the parent's LOCAL [0,1] box, but `composeRect` later
  // stretches that box by the parent's own world width and height separately.
  // Since a parent card is itself landscape, sizing to `baseAspect` directly
  // would let every nesting level re-multiply the horizontal stretch, so a few
  // levels deep a card collapses into an unreadable sliver. Pre-divide by the
  // parent aspect: once `composeRect` re-applies it, each card lands at
  // `baseAspect` on screen at any depth. Clamp so an extreme parent shape can't
  // invert or explode the local card. A card whose name needs more room than
  // `baseAspect` gives is widened (never shortened, so importance still sets its
  // height), and goes through the same pre-divide.
  const localAspect = (c: LayoutChild): number => {
    const want = c.name && c.kind ? Math.max(baseAspect, labelAspect(c.name, c.kind)) : baseAspect;
    return Math.min(8, Math.max(0.2, want / (parentAspect || 1)));
  };

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
    return { id: c.id, w: h * localAspect(c), h };
  });

  // Greedy row packing. The row target that fills the parent best depends on
  // how the card widths happen to break, so try a few shapes around the
  // aspect-matched grid estimate: `k` average cards per row, and `k` evenly
  // filled rows. Keep the one whose uniform fit is largest (the biggest
  // cards). Each try is one linear pass.
  const { cols, rows: estRows } = gridDimensions(n, parentAspect / CARD_ASPECT);
  const sumW = cards.reduce((s, c) => s + c.w, 0);
  const avgW = sumW / n;
  let best: PackedRows | null = null;
  const consider = (tryRows: PackedRows): void => {
    if (!best || tryRows.scale > best.scale + 1e-12) best = tryRows;
  };
  for (let k = Math.max(1, cols - COL_SEARCH); k <= Math.min(n, cols + COL_SEARCH); k++) {
    consider(packRows(cards, k * avgW + (k - 1) * gutter, gutter, false));
  }
  for (let r = Math.max(1, estRows - COL_SEARCH); r <= Math.min(n, estRows + COL_SEARCH); r++) {
    consider(packRows(cards, (sumW + (n - r) * gutter) / r, gutter, true));
  }
  const { rows, maxRowW, totalH, scale } = best!;
  const offX = (1 - maxRowW * scale) / 2;
  const offY = (1 - totalH * scale) / 2;

  let y = 0;
  for (const row of rows) {
    const h = rowH(row);
    let x = (maxRowW - rowW(row, gutter)) / 2; // centre the row -> staggered left edges
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
