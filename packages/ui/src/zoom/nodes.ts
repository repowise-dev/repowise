/**
 * Node-card and relation drawing on a 2D canvas. Browser code.
 *
 * Everything is drawn in screen-pixel space (the recursion in `draw-tree.ts`
 * resolves each node's screen rect from the camera), so text stays crisp at any
 * zoom instead of being scaled and blurred. Level-of-detail thresholds gate
 * label and summary drawing by the card's on-screen size.
 *
 * Visual language (Linear/Stripe idiom): cards are clean surfaces that float on
 * a faint dot-grid via a soft drop shadow and a hairline border. Role (entry /
 * hotspot / dead / on-flow) is shown as one small status dot, never a colored
 * frame, and detailed metrics live in the side panel rather than on the card, so
 * the canvas stays calm at a glance. Relations are deliberately low-emphasis: a
 * single neutral hue, thin, faded behind the cards.
 */

import type { Rect } from "./camera";
import { ARROW_SIZE_PX, EDGE_LINE_PX } from "./constants";
import type { EdgeRoute } from "./edges";
import type { ZoomPalette } from "./theme";
import type { ZoomNode } from "./types";

const LABEL_MIN_PX = 44; // draw the name once a card is at least this wide
const SUMMARY_MIN_PX = 260; // draw a one-line summary on large cards
const DOT_MIN_PX = 60; // draw the role status dot once there is room
const CORNER_PX = 12;
const RULE_MIN_PX = 96; // draw notebook ruled lines once the card is big enough
const RULE_GAP_PX = 22; // spacing between ruled lines (screen px)

/** Faint horizontal ruled lines inside a card, for a notebook-paper texture. */
function drawNotebookRules(
  ctx: CanvasRenderingContext2D,
  rect: Rect,
  radius: number,
  palette: ZoomPalette,
): void {
  ctx.save();
  roundRectPath(ctx, rect, radius);
  ctx.clip();
  ctx.strokeStyle = palette.rule;
  ctx.lineWidth = 1;
  const bottom = rect.y + rect.h;
  // Offset the first line below the title band; lines run edge to edge like ruled
  // paper and sit far enough under the text to stay invisible behind it.
  for (let y = rect.y + RULE_GAP_PX * 1.6; y < bottom - 3; y += RULE_GAP_PX) {
    const yy = Math.round(y) + 0.5; // crisp 1px rule
    ctx.beginPath();
    ctx.moveTo(rect.x, yy);
    ctx.lineTo(rect.x + rect.w, yy);
    ctx.stroke();
  }
  ctx.restore();
}

/** The single most salient role color for a node's status dot, or null. */
function roleColor(node: ZoomNode, palette: ZoomPalette): string | null {
  if (node.is_entry_point || node.metrics.entry_point_count > 0) return palette.entry;
  if (node.is_hotspot || node.metrics.hotspot_count > 0) return palette.hotspot;
  if (node.is_dead || node.metrics.dead_count > 0) return palette.dead;
  if (node.on_flow || node.metrics.on_flow_count > 0) return palette.flow;
  return null;
}

function roundRectPath(ctx: CanvasRenderingContext2D, r: Rect, radius: number): void {
  const rad = Math.max(0, Math.min(radius, r.w / 2, r.h / 2));
  ctx.beginPath();
  ctx.roundRect(r.x, r.y, r.w, r.h, rad);
}

function fitText(ctx: CanvasRenderingContext2D, text: string, maxW: number): string {
  if (ctx.measureText(text).width <= maxW) return text;
  const ell = "…";
  let lo = 0;
  let hi = text.length;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (ctx.measureText(text.slice(0, mid) + ell).width <= maxW) lo = mid;
    else hi = mid - 1;
  }
  return lo > 0 ? text.slice(0, lo) + ell : "";
}

export interface CardState {
  selected: boolean;
  hovered: boolean;
  /** During a pan we skip the (costlier) shadow so dragging stays smooth. */
  lowDetail: boolean;
}

/** Draw one node's card body in screen space, at the given alpha. */
export function drawCard(
  ctx: CanvasRenderingContext2D,
  rect: Rect,
  node: ZoomNode,
  palette: ZoomPalette,
  alpha: number,
  state: CardState,
  t: number,
): void {
  if (alpha <= 0) return;
  const { selected, hovered, lowDetail } = state;
  ctx.globalAlpha = alpha;

  const radius = Math.max(0, Math.min(CORNER_PX, rect.w / 2, rect.h / 2));
  roundRectPath(ctx, rect, radius);

  // Fill with a soft elevation shadow (skipped on tiny cards / during pans so
  // the frame stays cheap). The path is preserved across save/restore, so the
  // border below strokes the same rounded rect without inheriting the shadow.
  const elevate = !lowDetail && rect.w >= 56 && rect.h >= 36;
  ctx.save();
  if (elevate) {
    ctx.shadowColor = palette.shadow;
    ctx.shadowBlur = hovered ? 26 : 14;
    ctx.shadowOffsetY = hovered ? 7 : 3;
  }
  ctx.fillStyle = node.kind === "file" ? palette.nodeFill : palette.nodeFillAlt;
  ctx.fill();
  ctx.restore();

  // Notebook ruled-paper texture inside the card (skipped on tiny cards / pans).
  if (!lowDetail && rect.w >= RULE_MIN_PX && rect.h >= RULE_MIN_PX) {
    drawNotebookRules(ctx, rect, radius, palette);
  }

  // Hairline border; the hovered card firms up, the selected card gets an accent
  // ring. No role color on the frame (that lives in the status dot).
  ctx.lineWidth = selected ? 2 : 1;
  ctx.strokeStyle = selected
    ? palette.accent
    : hovered
      ? palette.nodeBorderHover
      : palette.nodeBorder;
  ctx.stroke();

  // Role status dot, top-right. One dot, most-salient role only.
  if (rect.w >= DOT_MIN_PX && rect.h >= 28) {
    const role = roleColor(node, palette);
    if (role) {
      ctx.beginPath();
      ctx.arc(rect.x + rect.w - 13, rect.y + 13, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = role;
      ctx.fill();
    }
  }

  // The node's own text recedes faster than its frame so that, mid-zoom, the
  // children fading in inside it read cleanly instead of overlapping its label.
  const textAlpha = alpha * Math.max(0, 1 - 2 * t);
  if (rect.w < LABEL_MIN_PX || rect.h < 16 || textAlpha <= 0.02) {
    ctx.globalAlpha = 1;
    return;
  }

  const pad = Math.min(12, rect.w * 0.06);
  const fontSize = Math.max(11, Math.min(17, rect.h * 0.16, rect.w * 0.09));
  ctx.globalAlpha = textAlpha;
  ctx.fillStyle = palette.nodeText;
  ctx.font = `600 ${fontSize}px ui-sans-serif, system-ui, sans-serif`;
  ctx.textBaseline = "top";
  // Leave room for the dot so a long name never collides with it.
  const labelMax = rect.w - pad * 2 - (rect.w >= DOT_MIN_PX ? 14 : 0);
  const label = fitText(ctx, node.name, labelMax);
  ctx.fillText(label, rect.x + pad, rect.y + pad);

  if (rect.w >= SUMMARY_MIN_PX && rect.h >= 96 && node.summary) {
    ctx.fillStyle = palette.textMuted;
    ctx.font = `400 ${Math.max(11, fontSize - 3)}px ui-sans-serif, system-ui, sans-serif`;
    const summary = fitText(ctx, node.summary, rect.w - pad * 2);
    ctx.fillText(summary, rect.x + pad, rect.y + pad + fontSize + 6);
  }

  ctx.globalAlpha = 1;
}

/** Coupling tier -> line width multiplier (kept subtle). */
function couplingWidth(coupling: string): number {
  if (coupling === "tight") return 1.5;
  if (coupling === "moderate") return 1.2;
  return 1;
}

function fillArrowHead(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  angle: number,
  size: number,
): void {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(-size, -size * 0.5);
  ctx.lineTo(-size, size * 0.5);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

/**
 * Draw an aggregated relation as a directed bezier connector (see `edges.ts` for
 * the routing). Relations are intentionally quiet: a single neutral hue, thin,
 * and faded so they read as a hint behind the cards rather than the main event.
 * `withArrow` is false for small boxes and unfocused edges, where a head would
 * just add clutter.
 */
export function drawEdge(
  ctx: CanvasRenderingContext2D,
  route: EdgeRoute,
  coupling: string,
  palette: ZoomPalette,
  alpha: number,
  withArrow: boolean,
): void {
  if (alpha <= 0.02) return;
  const { start, c1, c2, end, endAngle } = route;
  const color = coupling === "tight" ? palette.edgeStrong : palette.edge;
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = EDGE_LINE_PX * couplingWidth(coupling);
  // Stop the curve just shy of the head so the stroke and the fill do not overlap.
  const headBack = withArrow ? ARROW_SIZE_PX : 0;
  const ex = end.x - Math.cos(endAngle) * headBack;
  const ey = end.y - Math.sin(endAngle) * headBack;
  ctx.beginPath();
  ctx.moveTo(start.x, start.y);
  ctx.bezierCurveTo(c1.x, c1.y, c2.x, c2.y, ex, ey);
  ctx.stroke();

  if (withArrow) {
    ctx.fillStyle = color;
    fillArrowHead(ctx, end.x, end.y, endAngle, ARROW_SIZE_PX);
  }
  ctx.globalAlpha = 1;
}
