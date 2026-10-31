/**
 * Relation labels on the drawn arrows: "calls 13", or "calls 13 · imports 5"
 * when one pair of boxes is joined by several verbs. A C4 diagram says what
 * each line means on the line itself, so the canvas does too rather than
 * leaving the verb to the detail panel.
 *
 * Layout is pure (`layoutEdgeLabels`): each label sits at its arrow's bezier
 * midpoint and, when that collides with a card or a label already placed, is
 * nudged along the curve's normal, then along the curve, greedily, strongest
 * pair first. Where no spot clears every card (two cards a short gap apart),
 * the label may straddle their padding but never their text; covering card
 * text or another label is not allowed, so a label with no such spot is
 * dropped rather than stacked: an unreadable pile, or a caption over a card's
 * name, says less than a missing caption the hover card still gives. The
 * at-rest arrows (nothing focused) are stricter still: a label only where it
 * clears every card, since a level at rest should read calm.
 *
 * The renderer only paints labels on a settled frame (never mid-pan or
 * mid-zoom), and `edgeLabelLayout` keeps the last layout keyed by what it was
 * computed from, so the layout runs once per hover or selection change and a
 * repaint for any other reason reuses it.
 */

import type { Rect } from "./camera";
import { cardPadPx } from "./card-content";
import { cardTextCache } from "./card-measure";
import type { EdgeRoute, Point, RoutedEdge } from "./edges";
import type { ZoomPalette } from "./theme";

const LABEL_FONT_PX = 10;
const LABEL_FONT = `500 ${LABEL_FONT_PX}px ui-sans-serif, system-ui, sans-serif`;
const LABEL_PAD_X = 6;
export const LABEL_H = 16;
/** Clear space kept between two labels and from the viewport edge. */
const LABEL_GAP = 2;
/** Where along the curve a label may sit, preferred first. */
const T_STEPS = [0.5, 0.4, 0.6, 0.3, 0.7] as const;
/** Offsets along the normal, in label heights, preferred first. */
const NORMAL_STEPS = [0, 1.15, -1.15, 2.3, -2.3] as const;

/** Point on a cubic bezier at `t`. */
export function bezierPoint(r: EdgeRoute, t: number): Point {
  const u = 1 - t;
  const a = u * u * u;
  const b = 3 * u * u * t;
  const c = 3 * u * t * t;
  const d = t * t * t;
  return {
    x: a * r.start.x + b * r.c1.x + c * r.c2.x + d * r.end.x,
    y: a * r.start.y + b * r.c1.y + c * r.c2.y + d * r.end.y,
  };
}

/** Unit normal of a cubic bezier at `t` (the tangent turned 90 degrees). */
export function bezierNormal(r: EdgeRoute, t: number): Point {
  const u = 1 - t;
  const dx =
    3 * u * u * (r.c1.x - r.start.x) + 6 * u * t * (r.c2.x - r.c1.x) + 3 * t * t * (r.end.x - r.c2.x);
  const dy =
    3 * u * u * (r.c1.y - r.start.y) + 6 * u * t * (r.c2.y - r.c1.y) + 3 * t * t * (r.end.y - r.c2.y);
  const len = Math.hypot(dx, dy);
  return len > 0 ? { x: -dy / len, y: dx / len } : { x: 0, y: -1 };
}

/** One labelled box pair: every verb drawn between the two, merged. */
interface PairLabel {
  key: string;
  route: EdgeRoute;
  text: string;
  /** Sum of the pair's edge counts; strongest pairs claim space first. */
  weight: number;
}

/**
 * Merge the drawn edges into one label per unordered box pair, verbs ordered
 * by weight: "calls 13 · imports 5". The first (strongest) edge's route
 * carries the label, since the scene orders relations strongest first.
 */
export function pairLabels(edges: readonly RoutedEdge[]): PairLabel[] {
  const byPair = new Map<string, { route: EdgeRoute; verbs: Map<string, number> }>();
  for (const e of edges) {
    if (!e.label) continue;
    const key = e.sourceId < e.targetId ? `${e.sourceId}|${e.targetId}` : `${e.targetId}|${e.sourceId}`;
    let pair = byPair.get(key);
    if (!pair) byPair.set(key, (pair = { route: e.route, verbs: new Map() }));
    pair.verbs.set(e.label, (pair.verbs.get(e.label) ?? 0) + e.edgeCount);
  }
  const out: PairLabel[] = [];
  for (const [key, { route, verbs }] of byPair) out.push({ key, route, ...verbSummary(verbs) });
  return out.sort((a, b) => b.weight - a.weight || (a.key < b.key ? -1 : 1));
}

/**
 * One pair's verb counts as label text, strongest verb first (ties by name):
 * "calls 13 · imports 5", with the counts' total as the pair's weight.
 */
export function verbSummary(verbs: ReadonlyMap<string, number>): { text: string; weight: number } {
  const sorted = [...verbs.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  return {
    text: sorted.map(([verb, n]) => `${verb} ${n.toLocaleString("en-US")}`).join(" · "),
    weight: sorted.reduce((s, [, n]) => s + n, 0),
  };
}

interface PlacedLabel {
  text: string;
  rect: Rect;
}

function overlaps(a: Rect, b: Rect): boolean {
  return (
    a.x < b.x + b.w + LABEL_GAP &&
    b.x < a.x + a.w + LABEL_GAP &&
    a.y < b.y + b.h + LABEL_GAP &&
    b.y < a.y + a.h + LABEL_GAP
  );
}

function inside(r: Rect, area: Rect): boolean {
  return (
    r.x >= area.x + LABEL_GAP &&
    r.y >= area.y + LABEL_GAP &&
    r.x + r.w <= area.x + area.w - LABEL_GAP &&
    r.y + r.h <= area.y + area.h - LABEL_GAP
  );
}

/** Every spot a `w`-wide pill may take on `route`, preferred first. */
function candidates(route: EdgeRoute, w: number): Rect[] {
  const out: Rect[] = [];
  for (const t of T_STEPS) {
    const p = bezierPoint(route, t);
    const n = bezierNormal(route, t);
    for (const k of NORMAL_STEPS) {
      const cx = p.x + n.x * k * LABEL_H;
      const cy = p.y + n.y * k * LABEL_H;
      out.push({ x: cx - w / 2, y: cy - LABEL_H / 2, w, h: LABEL_H });
    }
  }
  return out;
}

/** The box around a set of rects. */
function bounds(rects: readonly Rect[]): Rect {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const r of rects) {
    x0 = Math.min(x0, r.x);
    y0 = Math.min(y0, r.y);
    x1 = Math.max(x1, r.x + r.w);
    y1 = Math.max(y1, r.y + r.h);
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

function clear(r: Rect, area: Rect, blockers: readonly Rect[]): boolean {
  return inside(r, area) && !blockers.some((b) => overlaps(b, r));
}

/** A card's text area: its rect less the padding the text keeps inside it. */
function textArea(c: Rect): Rect {
  const p = cardPadPx(c.w);
  return { x: c.x + p, y: c.y + p, w: Math.max(0, c.w - 2 * p), h: Math.max(0, c.h - 2 * p) };
}

/**
 * First spot clear of both placed labels and cards; failing that (unless
 * `strict`), the first clear of labels and of card text (straddling a card's
 * edge in a narrow gap); null when every spot collides or leaves the area.
 */
function placeOne(
  route: EdgeRoute,
  w: number,
  placed: readonly Rect[],
  cards: readonly Rect[],
  area: Rect,
  strict: boolean,
): Rect | null {
  const cands = candidates(route, w);
  const span = bounds(cands);
  const near = cards.filter((c) => overlaps(c, span));
  const blockers = [...placed, ...near];
  const text = [...placed, ...near.map(textArea)];
  const clean = cands.find((r) => clear(r, area, blockers));
  if (clean || strict) return clean ?? null;
  return cands.find((r) => clear(r, area, text)) ?? null;
}

/**
 * Greedy, strongest-first placement of one pill per pair inside `area` (the
 * visible part of the parent the arrows are clipped to), steering clear of
 * the drawn `cards` where it can (and always, when `strict`). Pure: `measure`
 * returns a string's width in px at the label font.
 */
export function layoutEdgeLabels(
  pairs: readonly PairLabel[],
  measure: (s: string) => number,
  area: Rect,
  cards: readonly Rect[] = [],
  strict = false,
): PlacedLabel[] {
  const placed: PlacedLabel[] = [];
  const taken: Rect[] = [];
  for (const pair of pairs) {
    const w = Math.ceil(measure(pair.text)) + LABEL_PAD_X * 2;
    const rect = placeOne(pair.route, w, taken, cards, area, strict);
    if (!rect) continue;
    placed.push({ text: pair.text, rect });
    taken.push(rect);
  }
  return placed;
}

/**
 * Single-slot memo: the last layout and the identities it was computed from
 * (scene, camera, viewport, focus, parent, verb filter, ...). The renderer keeps
 * each of those objects stable until it changes, so an identity match means
 * the arrows are exactly the ones already laid out.
 */
let memo: { key: readonly unknown[]; labels: PlacedLabel[] } | null = null;

function sameKey(a: readonly unknown[], b: readonly unknown[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/**
 * `layoutEdgeLabels` for this frame's arrows, reused while `key` is unchanged.
 * `cards` is a thunk so the screen rects of the cards to avoid are only
 * resolved when the layout actually runs.
 */
export function edgeLabelLayout(
  key: readonly unknown[],
  edges: readonly RoutedEdge[],
  measure: (s: string) => number,
  area: Rect,
  cards: () => readonly Rect[] = () => [],
  strict = false,
): PlacedLabel[] {
  if (memo && sameKey(memo.key, key)) return memo.labels;
  const labels = layoutEdgeLabels(pairLabels(edges), measure, area, cards(), strict);
  // Copied: the caller may reuse its key array frame to frame.
  memo = { key: key.slice(), labels };
  return labels;
}

/** Paint the arrow labels: a page-coloured pill with a hairline edge. */
export function drawEdgeLabels(
  ctx: CanvasRenderingContext2D,
  key: readonly unknown[],
  edges: readonly RoutedEdge[],
  area: Rect,
  palette: ZoomPalette,
  alpha: number,
  cards: () => readonly Rect[],
  strict = false,
): void {
  if (alpha <= 0.02 || edges.length === 0) return;
  const labels = edgeLabelLayout(
    key,
    edges,
    (s) => cardTextCache.width(ctx, LABEL_FONT, s),
    area,
    cards,
    strict,
  );
  if (labels.length === 0) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.lineWidth = 1;
  ctx.font = LABEL_FONT;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const { text, rect } of labels) {
    ctx.beginPath();
    ctx.roundRect(rect.x, rect.y, rect.w, rect.h, LABEL_H / 2);
    ctx.fillStyle = palette.bg;
    ctx.fill();
    ctx.strokeStyle = palette.nodeBorder;
    ctx.stroke();
    ctx.fillStyle = palette.textSecondary;
    ctx.fillText(text, rect.x + rect.w / 2, rect.y + rect.h / 2 + 0.5);
  }
  ctx.restore();
}
