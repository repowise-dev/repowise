/**
 * Per-parent pack layout. Pure, browser-free and unit-testable.
 *
 * The backend ships a containment tree and no placement. Every card is sized by
 * importance (more central = bigger, within bounds so nothing is unreadably
 * small) and the cards are row-packed into centred, variable-width rows, the
 * whole cluster uniform-fit (aspect-preserving) into the parent so cards keep a
 * single landscape shape. Where the parent's children link to each other, the
 * rows follow the dependency direction: callers above callees, each row ordered
 * to cut crossings (see `dependencyOrder`, `uncross`). Children the caller
 * sets aside (the test layer, from `testLayerIds` in `scene.ts`) get a column
 * of their own. The rect emitted is in parent `[0,1]` space, which is what the
 * clip-and-scale composition in `geometry.ts` consumes.
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

/** A sibling link the layout ranks by (the slice of a `ZoomRelation` it reads). */
export interface LayoutRelation {
  source_id: string;
  target_id: string;
  edge_count: number;
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

/** Directed link weights between siblings: `out.get(a)?.get(b)` is a -> b, `into` the reverse. */
interface SiblingLinks {
  out: Map<string, Map<string, number>>;
  into: Map<string, Map<string, number>>;
}

function addWeight(m: Map<string, Map<string, number>>, a: string, b: string, w: number): void {
  let row = m.get(a);
  if (!row) m.set(a, (row = new Map()));
  row.set(b, (row.get(b) ?? 0) + w);
}

/** The links among `children`, or null when none join two of them. */
function siblingLinks(
  children: readonly LayoutChild[],
  relations: readonly LayoutRelation[],
): SiblingLinks | null {
  if (relations.length === 0) return null;
  const ids = new Set(children.map((c) => c.id));
  const links: SiblingLinks = { out: new Map(), into: new Map() };
  for (const r of relations) {
    if (r.source_id === r.target_id || !(r.edge_count > 0)) continue;
    if (!ids.has(r.source_id) || !ids.has(r.target_id)) continue;
    addWeight(links.out, r.source_id, r.target_id, r.edge_count);
    addWeight(links.into, r.target_id, r.source_id, r.edge_count);
  }
  return links.out.size > 0 ? links : null;
}

const EPS = 1e-9;

function total(m: ReadonlyMap<string, number> | undefined): number {
  let s = 0;
  if (m) for (const w of m.values()) s += w;
  return s;
}

/**
 * Callers before callees: a weighted greedy feedback-arc-set order (Eades, Lin
 * and Smyth). Sinks peel off to the back and sources to the front; when only
 * cycles are left, the child whose outgoing weight most exceeds its incoming
 * goes next, so a cycle breaks at its weakest links. Rank breaks ties. Children
 * with no links follow in rank order, into the bottom rows.
 *
 * Packing this order into rows is a layered layout with the layer count set by
 * the parent's shape, not the graph's depth, so the fit and the importance
 * sizing are untouched. Ceiling: a link may skip rows or run within one, and
 * crossings are cut per row (see `uncross`), not minimised globally. elkjs
 * (already a dependency) does a full layered pass, but it is async while the
 * scene builds synchronously per parent, and its layers would not fit the
 * parent's aspect; reach for it only if long skipped links start to read badly.
 * Cost: linear in the parent's links plus quadratic in its linked children, a
 * few microseconds per relation on a cold scene build; a re-layout repacks
 * only the root.
 */
function dependencyOrder(byRank: readonly LayoutChild[], { out, into }: SiblingLinks): LayoutChild[] {
  const linked = byRank.filter((c) => out.has(c.id) || into.has(c.id));
  const n = linked.length;
  // Index arrays, not id maps: this loop is quadratic in the linked children.
  const at = new Map(linked.map((c, i) => [c.id, i] as const));
  const outW = new Float64Array(n);
  const inW = new Float64Array(n);
  const live = new Uint8Array(n).fill(1);
  linked.forEach((c, i) => {
    outW[i] = total(out.get(c.id));
    inW[i] = total(into.get(c.id));
  });
  const take = (i: number): void => {
    live[i] = 0;
    for (const [u, w] of out.get(linked[i]!.id) ?? []) inW[at.get(u)!]! -= w;
    for (const [u, w] of into.get(linked[i]!.id) ?? []) outW[at.get(u)!]! -= w;
  };
  const head: LayoutChild[] = [];
  const tail: LayoutChild[] = []; // reversed: the first sink taken ends last
  for (let left = n; left > 0; left--) {
    let pick = -1;
    // The least important sink first, so the more important ones end higher.
    for (let i = n - 1; i >= 0; i--) {
      if (live[i] && outW[i]! <= EPS) {
        pick = i;
        break;
      }
    }
    if (pick >= 0) {
      take(pick);
      tail.push(linked[pick]!);
      continue;
    }
    let bestDelta = -Infinity;
    for (let i = 0; i < n; i++) {
      if (!live[i]) continue;
      if (inW[i]! <= EPS) {
        pick = i;
        break;
      }
      if (outW[i]! - inW[i]! > bestDelta + EPS) {
        bestDelta = outW[i]! - inW[i]!;
        pick = i;
      }
    }
    take(pick);
    head.push(linked[pick]!);
  }
  const unlinked = byRank.filter((c) => !out.has(c.id) && !into.has(c.id));
  return [...head, ...tail.reverse(), ...unlinked];
}

/**
 * Reorder each row by the barycentre of its links into the rows around it: a
 * sweep down (each row against everything above it), one back up (against
 * everything below) and a last one down. Row widths do not change, so the fit
 * holds. A card with no link into the reference rows keeps its place.
 */
function uncross(rows: Packed[][], { out, into }: SiblingLinks, maxRowW: number, gutter: number): void {
  if (rows.length < 2) return;
  const pos = new Map<string, number>();
  const rowOf = new Map<string, number>();
  const place = (row: readonly Packed[]): void => {
    let x = (maxRowW - rowW(row, gutter)) / 2;
    for (const c of row) {
      pos.set(c.id, x + c.w / 2);
      x += c.w + gutter;
    }
  };
  rows.forEach((row, i) => {
    place(row);
    for (const c of row) rowOf.set(c.id, i);
  });
  const key = new Map<string, number>();
  const sweep = (i: number, above: boolean): void => {
    const row = rows[i]!;
    for (const c of row) {
      let sw = 0;
      let sx = 0;
      const add = (m: Map<string, number> | undefined): void => {
        for (const [u, w] of m ?? []) {
          const r = rowOf.get(u)!;
          if (above ? r >= i : r <= i) continue;
          sw += w;
          sx += w * pos.get(u)!;
        }
      };
      add(out.get(c.id));
      add(into.get(c.id));
      key.set(c.id, sw > 0 ? sx / sw : pos.get(c.id)!);
    }
    row.sort((a, b) => key.get(a.id)! - key.get(b.id)!);
    place(row);
  };
  for (let i = 1; i < rows.length; i++) sweep(i, true);
  for (let i = rows.length - 2; i >= 0; i--) sweep(i, false);
  for (let i = 1; i < rows.length; i++) sweep(i, true);
}

/** Share of the parent's width a set-aside column takes, and the gap before it. */
const ASIDE_FRAC = 0.14;
const ASIDE_GAP = 0.02;

/**
 * Lay `children` out inside the unit parent box. Children are ranked by
 * dependency direction over `relations` (their sibling links; see
 * `dependencyOrder`), falling back to `sibling_rank` (most important first, so
 * the biggest cards read top-left) where no links decide; sized by importance
 * normalised across the sibling set, widened where a long name needs it, then
 * greedily row-packed to ~`cols` cards per row. Rows are centred (so left edges
 * stagger), each row is reordered to cut crossings, and each card is centred
 * in its row band (so top edges stagger); the whole cluster is uniform-fit
 * into `[0,1]²`. Children in `asideIds` go to a column on the right, and
 * their links leave the ranking: tests call into almost every layer, so ranked
 * with the rest they would sit on top of the map. Returns each child's rect in
 * parent `[0,1]` space. Deterministic: stable order, ties broken by id; no randomness.
 */
export function packLayout(
  children: readonly LayoutChild[],
  parentAspect: number,
  opts: PackLayoutOptions = {},
  relations: readonly LayoutRelation[] = [],
  asideIds?: ReadonlySet<string>,
): Map<string, Rect> {
  const aside = asideIds ? children.filter((c) => asideIds.has(c.id)) : [];
  if (aside.length === 0 || aside.length === children.length) {
    return packRanked(children, parentAspect, opts, relations);
  }
  // Each side packs into its own column, then is squeezed into it along x.
  // Packing to the column's aspect keeps the parentAspect pre-divide exact:
  // `composeRect` re-applies the parent's width, the squeeze its share of it.
  const mainFrac = 1 - ASIDE_FRAC - ASIDE_GAP;
  const out = packRanked(
    children.filter((c) => !asideIds!.has(c.id)),
    parentAspect * mainFrac,
    opts,
    relations,
  );
  let top = Infinity;
  for (const r of out.values()) {
    r.x *= mainFrac;
    r.w *= mainFrac;
    top = Math.min(top, r.y);
  }
  const side = packRanked(aside, parentAspect * ASIDE_FRAC, opts, []);
  let sideTop = Infinity;
  let sideBottom = -Infinity;
  for (const r of side.values()) {
    sideTop = Math.min(sideTop, r.y);
    sideBottom = Math.max(sideBottom, r.y + r.h);
  }
  // Top-aligned with the main cluster (a side note, not a centred peer), but
  // never pushed out of the box.
  const dy = Math.min(top - sideTop, 1 - sideBottom);
  for (const [id, r] of side) {
    out.set(id, { x: 1 - ASIDE_FRAC + r.x * ASIDE_FRAC, y: r.y + dy, w: r.w * ASIDE_FRAC, h: r.h });
  }
  return out;
}

function packRanked(
  children: readonly LayoutChild[],
  parentAspect: number,
  opts: PackLayoutOptions,
  relations: readonly LayoutRelation[],
): Map<string, Rect> {
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

  const byRank = [...children].sort(
    (a, b) => a.sibling_rank - b.sibling_rank || (a.id < b.id ? -1 : 1),
  );
  const links = siblingLinks(byRank, relations);
  const ordered = links ? dependencyOrder(byRank, links) : byRank;

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
  if (links) uncross(rows, links, maxRowW, gutter);
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
