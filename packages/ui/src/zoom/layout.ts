/**
 * Per-parent grid layout. Pure, browser-free and unit-testable.
 *
 * The backend ships a containment tree but its layout rects are a squarified
 * treemap (space-filling, extreme aspect ratios). We ignore those and lay each
 * parent's children out client-side instead: a near-uniform grid of separated
 * boxes. Columns/rows track the parent's world aspect so cells stay near-square
 * at any depth, a whitespace gutter sits between every box, and box size encodes
 * importance only lightly (a gentle shrink toward the cell centre) so aspect
 * ratios stay uniform. Keeping the layout in the renderer means it iterates with
 * no re-index, and the rect it emits is still in parent `[0,1]` space, so the
 * clip-and-scale composition in `geometry.ts` is unchanged.
 */

import type { Rect } from "./camera";
import { GRID_GUTTER_FRACTION, IMPORTANCE_SCALE_MIN } from "./constants";

/** The minimal slice of a node the layout needs (placement order + sizing). */
export interface LayoutChild {
  id: string;
  sibling_rank: number;
  importance: number;
}

export interface GridLayoutOptions {
  /** Whitespace channel as a fraction of each cell axis (per side). */
  gutter?: number;
  /** Least-important box shrinks to this fraction of its cell (1 = uniform). */
  importanceScaleMin?: number;
}

/**
 * Choose a grid whose column/row ratio tracks the parent's world aspect, so the
 * resulting cells are as close to square as the child count allows. Returns the
 * tightened dimensions (no empty trailing column).
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
  // that many rows. This can narrow the column count (not only trim one empty
  // trailing column), which is intended: fewer, fuller columns read better than
  // a sparse final column.
  return { cols: Math.ceil(count / rows), rows };
}

/**
 * Place `children` into a near-uniform grid inside the unit parent box. Children
 * are ordered by `sibling_rank` (most important first, top-left), each centred
 * in its cell, inset by the gutter, and shrunk lightly by importance. A partial
 * last row is centred horizontally. Returns each child's rect in parent `[0,1]`
 * space. Deterministic: stable order, ties broken by id.
 */
export function gridLayout(
  children: readonly LayoutChild[],
  parentAspect: number,
  opts: GridLayoutOptions = {},
): Map<string, Rect> {
  const gutter = opts.gutter ?? GRID_GUTTER_FRACTION;
  const scaleMin = opts.importanceScaleMin ?? IMPORTANCE_SCALE_MIN;
  const out = new Map<string, Rect>();
  const n = children.length;
  if (n === 0) return out;

  const ordered = [...children].sort(
    (a, b) => a.sibling_rank - b.sibling_rank || (a.id < b.id ? -1 : 1),
  );
  const { cols, rows } = gridDimensions(n, parentAspect);
  const cellW = 1 / cols;
  const cellH = 1 / rows;
  // Per-axis gutters: equal-looking channels in world space because the cells
  // are near-square there (cellW * parentW ~= cellH * parentH).
  const gx = gutter * cellW;
  const gy = gutter * cellH;

  for (let i = 0; i < n; i++) {
    const child = ordered[i]!;
    const row = Math.floor(i / cols);
    const col = i % cols;
    const itemsInRow = row === rows - 1 ? n - row * cols : cols;
    const rowShift = ((cols - itemsInRow) * cellW) / 2; // centre a partial last row

    const cellX = col * cellW + rowShift;
    const cellY = row * cellH;
    const innerW = cellW - 2 * gx;
    const innerH = cellH - 2 * gy;

    const imp = Number.isFinite(child.importance)
      ? Math.min(1, Math.max(0, child.importance))
      : 0;
    const scale = scaleMin + (1 - scaleMin) * imp;
    const w = innerW * scale;
    const h = innerH * scale;
    out.set(child.id, {
      x: cellX + gx + (innerW - w) / 2,
      y: cellY + gy + (innerH - h) / 2,
      w,
      h,
    });
  }
  return out;
}
