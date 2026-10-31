/**
 * Drawing a parent's external lane (see `externals.ts`): dashed C4 cards in
 * the rail, the "+N more" chip, and the connectors from the focused card.
 * Browser code.
 *
 * The lane's geometry is precomputed in lane units, so a frame only scales it
 * by the parent's on-screen width into reused scratch objects: no per-frame
 * allocation beyond the hit-test entries every drawn card gets.
 *
 * Focus works both ways. Hovering (or selecting) a child lights the externals
 * it reaches and draws its connectors, dimming the rest; hovering an external
 * card draws a connector from every child that reaches it.
 */

import type { Rect } from "./camera";
import { font, textWidth, wrap } from "./card-measure";
import { ARROW_MIN_BOX_PX, EDGE_FOCUS_ALPHA, EDGE_MAX_PER_PARENT } from "./constants";
import type { EdgeRoute, Point } from "./edges";
import { drawExternalGlyph } from "./external-glyph";
import { type ExternalCard, type ExternalLane, type ExternalLink, LANE_UNITS } from "./externals";
import { drawEdge } from "./nodes";
import { type DrawStats, pushPick } from "./pick";
import type { ZoomPalette } from "./theme";

/** Below this on-screen height a lane's cards are not drawn (nor its connectors). */
const EXTERNAL_MIN_PX = 14;
/** Alpha of the externals the focused card does not reach. */
const DIM_ALPHA = 0.35;
/** Connectors to the outside are dashed, like the cards they end on. */
const LINK_DASH = [5, 4];
const CARD_DASH = [4, 3];
const NO_DASH: number[] = [];

const box: Rect = { x: 0, y: 0, w: 0, h: 0 };
const pt = (): Point => ({ x: 0, y: 0 });
const route: EdgeRoute = { start: pt(), c1: pt(), c2: pt(), end: pt(), endAngle: 0 };

/** A lane rect on screen, into the shared scratch box. */
function onScreen(r: Rect, frame: Rect): Rect {
  const k = frame.w / LANE_UNITS;
  box.x = frame.x + r.x * k;
  box.y = frame.y + r.y * k;
  box.w = r.w * k;
  box.h = r.h * k;
  return box;
}

function mapPoint(out: Point, p: Point, frame: Rect, k: number): void {
  out.x = frame.x + p.x * k;
  out.y = frame.y + p.y * k;
}

/** The connectors the focus lights in this lane, or undefined. */
function focusedLinks(lane: ExternalLane, focusId: string | null): ExternalLink[] | undefined {
  if (focusId === null) return undefined;
  return lane.bySource.get(focusId) ?? lane.byTarget.get(focusId);
}

function reaches(links: readonly ExternalLink[], card: ExternalCard): boolean {
  for (const l of links) if (l.card === card) return true;
  return false;
}

function cardPx(lane: ExternalLane, frame: Rect): number {
  const first = lane.cards[0];
  return first ? (first.rect.h * frame.w) / LANE_UNITS : 0;
}

/**
 * Connectors from the focus, strongest first and capped like relations.
 * Drawn before the children so they pass under the cards they cross.
 */
export function drawExternalLinks(
  ctx: CanvasRenderingContext2D,
  lane: ExternalLane,
  frame: Rect,
  palette: ZoomPalette,
  alpha: number,
  focusId: string | null,
): void {
  const links = focusedLinks(lane, focusId);
  if (!links || cardPx(lane, frame) < EXTERNAL_MIN_PX) return;
  const k = frame.w / LANE_UNITS;
  const withArrow = cardPx(lane, frame) * 2 >= ARROW_MIN_BOX_PX;
  ctx.setLineDash(LINK_DASH);
  const n = Math.min(links.length, EDGE_MAX_PER_PARENT);
  for (let i = 0; i < n; i++) {
    const l = links[i]!;
    mapPoint(route.start, l.route.start, frame, k);
    mapPoint(route.c1, l.route.c1, frame, k);
    mapPoint(route.c2, l.route.c2, frame, k);
    mapPoint(route.end, l.route.end, frame, k);
    route.endAngle = l.route.endAngle;
    drawEdge(ctx, route, l.coupling, palette, alpha * EDGE_FOCUS_ALPHA, withArrow);
  }
  ctx.setLineDash(NO_DASH);
}

/** The dashed outline on the page ground; the accent rings a lit card. */
function drawCardFrame(
  ctx: CanvasRenderingContext2D,
  r: Rect,
  palette: ZoomPalette,
  alpha: number,
  active: boolean,
): void {
  ctx.globalAlpha = alpha;
  ctx.beginPath();
  ctx.roundRect(r.x, r.y, r.w, r.h, Math.min(10, r.h / 3));
  // The page ground, not a card fill: an external is a hole in the system
  // boundary, and the grid stops at its edge.
  ctx.fillStyle = palette.bg;
  ctx.fill();
  ctx.setLineDash(CARD_DASH);
  ctx.lineWidth = active ? 1.5 : 1;
  ctx.strokeStyle = active ? palette.accent : palette.textMuted;
  ctx.stroke();
  ctx.setLineDash(NO_DASH);
}

/** The import count in whatever form fits `room`, or "" when neither does. */
function fittedCount(ctx: CanvasRenderingContext2D, card: ExternalCard, px: number, room: number): string {
  if (textWidth(ctx, 400, px, card.count) <= room) return card.count;
  return textWidth(ctx, 400, px, card.countShort) <= room ? card.countShort : "";
}

/** Kind on the left, the import count right-aligned, at `y` across `maxW`. */
function drawMetaRow(
  ctx: CanvasRenderingContext2D,
  card: ExternalCard,
  palette: ZoomPalette,
  px: number,
  x: number,
  y: number,
  maxW: number,
): void {
  const kind = wrap(ctx, 400, px, card.kind, maxW, 1, "words")[0] ?? "";
  const count = fittedCount(ctx, card, px, maxW - textWidth(ctx, 400, px, kind) - px);
  ctx.font = font(400, px);
  ctx.fillStyle = palette.textMuted;
  ctx.fillText(kind, x, y);
  if (!count) return;
  ctx.textAlign = "right";
  ctx.fillText(count, x + maxW, y);
  ctx.textAlign = "left";
}

function drawCard(
  ctx: CanvasRenderingContext2D,
  r: Rect,
  card: ExternalCard,
  palette: ZoomPalette,
  alpha: number,
  active: boolean,
): void {
  drawCardFrame(ctx, r, palette, alpha, active);
  if (r.w < 40 || r.h < 18) return;

  const titlePx = Math.round(Math.min(14, Math.max(10, r.h * 0.24)));
  const metaPx = Math.max(9, titlePx - 2);
  const pad = Math.round(Math.min(10, r.w * 0.06));
  const textH = titlePx * 1.3 + metaPx * 1.3;
  const twoRows = r.h >= textH + pad;
  const top = r.y + (r.h - (twoRows ? textH : titlePx)) / 2;
  let x = r.x + pad;
  if (r.w >= 72) {
    ctx.strokeStyle = palette.textSecondary;
    ctx.lineWidth = Math.max(1, titlePx * 0.1);
    drawExternalGlyph(ctx, x, top, titlePx, card.external.kind);
    x += titlePx + Math.round(titlePx * 0.45);
  }
  const maxW = r.x + r.w - pad - x;
  ctx.textBaseline = "top";
  ctx.textAlign = "left";
  const title = wrap(ctx, 600, titlePx, card.title, maxW, 1, "words")[0];
  ctx.font = font(600, titlePx);
  ctx.fillStyle = palette.nodeText;
  if (title) ctx.fillText(title, x, top);
  if (twoRows) drawMetaRow(ctx, card, palette, metaPx, x, top + titlePx * 1.3, maxW);
}

function drawMore(
  ctx: CanvasRenderingContext2D,
  r: Rect,
  label: string,
  palette: ZoomPalette,
  alpha: number,
): void {
  const px = Math.round(Math.min(12, Math.max(9, r.h * 0.5)));
  if (r.w < 40 || r.h < px) return;
  ctx.globalAlpha = alpha;
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  ctx.font = font(600, px);
  ctx.fillStyle = palette.textMuted;
  ctx.fillText(label, r.x + r.w / 2, r.y + r.h / 2);
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
}

/**
 * The lane's cards and chip at `alpha` (the children's fade-in), each card a
 * hit-test entry at `depth` so hovering one lights who reaches it.
 */
export function drawExternalCards(
  ctx: CanvasRenderingContext2D,
  lane: ExternalLane,
  frame: Rect,
  palette: ZoomPalette,
  alpha: number,
  focusId: string | null,
  stats: DrawStats,
  depth: number,
): void {
  if (cardPx(lane, frame) < EXTERNAL_MIN_PX) return;
  const links = focusedLinks(lane, focusId);
  for (const card of lane.cards) {
    const r = onScreen(card.rect, frame);
    const active = card.id === focusId || (links !== undefined && reaches(links, card));
    const a = links && !active ? alpha * DIM_ALPHA : alpha;
    drawCard(ctx, r, card, palette, a, active);
    pushPick(stats, card.id, r, depth, lane);
  }
  const moreAlpha = links ? alpha * DIM_ALPHA : alpha;
  if (lane.more) drawMore(ctx, onScreen(lane.more.rect, frame), lane.more.label, palette, moreAlpha);
  ctx.globalAlpha = 1;
}

/** Screen rects of the lane's drawn cards and chip (none when too small to draw). */
export function externalCardRects(lane: ExternalLane, frame: Rect): Rect[] {
  if (cardPx(lane, frame) < EXTERNAL_MIN_PX) return [];
  const out = lane.cards.map((c) => ({ ...onScreen(c.rect, frame) }));
  if (lane.more) out.push({ ...onScreen(lane.more.rect, frame) });
  return out;
}
