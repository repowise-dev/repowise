/**
 * Which node is under a point, without walking every node.
 *
 * A uniform grid over the field, built once per layout: each node is listed
 * in every cell its disc touches, so a query reads the few cells around the
 * pointer instead of eight thousand discs. Cells are flat typed arrays (a
 * compressed row list), which keeps the build one counting pass and one fill
 * pass with no per-cell allocation.
 */

export interface HitNode {
  x: number;
  y: number;
  r: number;
}

export interface HitGrid {
  x0: number;
  y0: number;
  cell: number;
  cols: number;
  rows: number;
  /** `start[c]..start[c + 1]` indexes `items` for cell `c`. */
  start: Int32Array;
  items: Int32Array;
  xs: Float64Array;
  ys: Float64Array;
  rs: Float64Array;
}

/** Cells along the longer side, at most. Bounds memory on a sparse field. */
const MAX_CELLS_PER_SIDE = 256;

export function buildHitGrid(nodes: readonly HitNode[]): HitGrid {
  const n = nodes.length;
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);
  const rs = new Float64Array(n);
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let maxR = 0;
  for (let i = 0; i < n; i++) {
    const nd = nodes[i]!;
    xs[i] = nd.x;
    ys[i] = nd.y;
    rs[i] = nd.r;
    minX = Math.min(minX, nd.x - nd.r);
    minY = Math.min(minY, nd.y - nd.r);
    maxX = Math.max(maxX, nd.x + nd.r);
    maxY = Math.max(maxY, nd.y + nd.r);
    maxR = Math.max(maxR, nd.r);
  }
  if (n === 0) {
    return { x0: 0, y0: 0, cell: 1, cols: 0, rows: 0, start: new Int32Array(1), items: new Int32Array(0), xs, ys, rs };
  }
  const w = Math.max(maxX - minX, 1e-9);
  const h = Math.max(maxY - minY, 1e-9);
  // About one node per cell on a dense field, never finer than a big node.
  const cell = Math.max(Math.sqrt((w * h) / n), maxR, Math.max(w, h) / MAX_CELLS_PER_SIDE);
  const cols = Math.max(1, Math.ceil(w / cell));
  const rows = Math.max(1, Math.ceil(h / cell));
  const counts = new Int32Array(cols * rows + 1);
  const span = (i: number) => {
    const c0 = Math.max(0, Math.floor((xs[i]! - rs[i]! - minX) / cell));
    const c1 = Math.min(cols - 1, Math.floor((xs[i]! + rs[i]! - minX) / cell));
    const r0 = Math.max(0, Math.floor((ys[i]! - rs[i]! - minY) / cell));
    const r1 = Math.min(rows - 1, Math.floor((ys[i]! + rs[i]! - minY) / cell));
    return [c0, c1, r0, r1] as const;
  };
  for (let i = 0; i < n; i++) {
    const [c0, c1, r0, r1] = span(i);
    for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) counts[r * cols + c + 1]!++;
  }
  const start = new Int32Array(cols * rows + 1);
  for (let c = 0; c < cols * rows; c++) start[c + 1] = start[c]! + counts[c + 1]!;
  const fill = start.slice(0, cols * rows);
  const items = new Int32Array(start[cols * rows]!);
  for (let i = 0; i < n; i++) {
    const [c0, c1, r0, r1] = span(i);
    for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) items[fill[r * cols + c]!++] = i;
  }
  return { x0: minX, y0: minY, cell, cols, rows, start, items, xs, ys, rs };
}

/**
 * The node under `(x, y)`, or -1. `slop` widens every disc by that much, in
 * the same units, so a one-pixel node stays hittable at a low zoom. When
 * several qualify, the one the point sits deepest inside wins, and between
 * near misses the one whose edge is closest.
 */
export function hitTest(grid: HitGrid, x: number, y: number, slop = 0): number {
  if (grid.cols === 0) return -1;
  const { x0, y0, cell, cols, rows, start, items, xs, ys, rs } = grid;
  const c0 = Math.max(0, Math.floor((x - slop - x0) / cell));
  const c1 = Math.min(cols - 1, Math.floor((x + slop - x0) / cell));
  const r0 = Math.max(0, Math.floor((y - slop - y0) / cell));
  const r1 = Math.min(rows - 1, Math.floor((y + slop - y0) / cell));
  let best = -1;
  let bestGap = Infinity;
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) {
      const k = r * cols + c;
      for (let j = start[k]!; j < start[k + 1]!; j++) {
        const i = items[j]!;
        const gap = Math.hypot(x - xs[i]!, y - ys[i]!) - rs[i]!;
        if (gap <= slop && (gap < bestGap || (gap === bestGap && i < best))) {
          best = i;
          bestGap = gap;
        }
      }
    }
  }
  return best;
}
