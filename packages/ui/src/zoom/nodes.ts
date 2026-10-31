/**
 * Node-card and relation drawing on a 2D canvas. Browser code.
 *
 * Everything is drawn in screen-pixel space (the recursion in `draw-tree.ts`
 * resolves each node's screen rect from the camera), so text stays crisp at any
 * zoom instead of being scaled and blurred. The card's text zones (title,
 * chips, summary, footer) are painted by `card-text.ts`, which discloses detail
 * by the card's on-screen size.
 *
 * Visual language (Linear/Stripe idiom): cards are clean surfaces that float on
 * a faint dot-grid via a soft drop shadow and a hairline border. Role (entry /
 * hotspot / dead / on-flow) is shown as one small status dot, never a colored
 * frame, and detailed metrics live in the side panel rather than on the card, so
 * the canvas stays calm at a glance. Relations are deliberately low-emphasis: a
 * single neutral hue, thin, faded behind the cards.
 */

import type { Rect } from "./camera";
import type { CardPreview } from "./card-preview";
import { drawCardText } from "./card-text";
import { hasRole } from "./node-signals";
import { ARROW_SIZE_PX, EDGE_LINE_PX } from "./constants";
import type { EdgeRoute } from "./edges";
import type { ZoomPalette } from "./theme";
import type { ZoomNode } from "./types";

const DOT_MIN_PX = 60; // draw the role status dot once there is room
const CORNER_PX = 12;
const TEXTURE_MIN_PX = 96; // paint the paper texture once the card is big enough

/**
 * Paint the shared ruled-paper photo inside a card, washed by the card's own
 * per-theme wash so the tint and text contrast survive. This is the canvas
 * equivalent of the KG cards' `--kg-card-texture` (paper photo under a
 * translucent wash), reusing the very same asset via `PaperTexture`. The ruled
 * lines and grain come from the photo, so no lines are drawn procedurally.
 *
 * Order matters: the opaque paper tile is laid down first, then the wash pulls
 * the composite back toward the card color. Wrapped in save/restore, so the
 * clip and the temporary alpha are both undone for the caller.
 */
function drawPaperTexture(
  ctx: CanvasRenderingContext2D,
  rect: Rect,
  radius: number,
  paper: CanvasPattern,
  palette: ZoomPalette,
  alpha: number,
): void {
  ctx.save();
  roundRectPath(ctx, rect, radius);
  ctx.clip();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = paper;
  ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
  // The wash carries its own per-theme alpha (the `--color-zoom-card-paper-wash`
  // token); globalAlpha here only folds in the card's body-fade.
  ctx.globalAlpha = alpha;
  ctx.fillStyle = palette.paperWash;
  ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
  ctx.restore();
}

/**
 * The role dot's colour, or null when a node carries no role.
 *
 * One hue, deliberately. A per-role palette collides with the health dot in the
 * footer — `palette.entry` and `palette.healthGood` are both `--color-success`,
 * so one green would mean "has an entry point" and another "Good", forty pixels
 * apart on the same card. The band colours belong to health.
 *
 * The role dot says only "there is something here"; `nodeRoles` in
 * `node-signals.ts` names every applicable role in words.
 */
function roleColor(node: ZoomNode, palette: ZoomPalette): string | null {
  return hasRole(node) ? palette.accent : null;
}

function roundRectPath(ctx: CanvasRenderingContext2D, r: Rect, radius: number): void {
  const rad = Math.max(0, Math.min(radius, r.w / 2, r.h / 2));
  ctx.beginPath();
  ctx.roundRect(r.x, r.y, r.w, r.h, rad);
}

export interface CardState {
  selected: boolean;
  hovered: boolean;
  /** During a pan we skip the (costlier) shadow so dragging stays smooth. */
  lowDetail: boolean;
  /** While the camera zooms or flies we skip only the shadow (a blur pass per card). */
  motion?: boolean;
  /** A container's "contains" list for the card body (see `card-preview.ts`). */
  preview?: CardPreview | null;
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
  paper: CanvasPattern | null,
): void {
  if (alpha <= 0) return;
  const { selected, hovered, lowDetail } = state;
  ctx.globalAlpha = alpha;

  const radius = Math.max(0, Math.min(CORNER_PX, rect.w / 2, rect.h / 2));
  roundRectPath(ctx, rect, radius);

  // Base fill with a soft elevation shadow (skipped on tiny cards / during pans
  // so the frame stays cheap). This lays down the card silhouette + shadow; the
  // paper texture below is painted over it and the border re-strokes the rect.
  const elevate = !lowDetail && !state.motion && rect.w >= 56 && rect.h >= 36;
  ctx.save();
  if (elevate) {
    ctx.shadowColor = palette.shadow;
    ctx.shadowBlur = hovered ? 26 : 14;
    ctx.shadowOffsetY = hovered ? 7 : 3;
  }
  ctx.fillStyle = node.kind === "file" ? palette.nodeFill : palette.nodeFillAlt;
  ctx.fill();
  ctx.restore();

  // Ruled-paper texture (shared KG asset) inside the card, skipped on tiny cards
  // / pans and whenever the texture has not loaded yet (then the base fill above
  // stands in). The photo supplies the ruled lines, so none are drawn by hand.
  if (paper && !lowDetail && rect.w >= TEXTURE_MIN_PX && rect.h >= TEXTURE_MIN_PX) {
    drawPaperTexture(ctx, rect, radius, paper, palette, alpha);
  }

  // Hairline border; the hovered card firms up, the selected card gets an accent
  // ring. No role color on the frame (that lives in the status dot). Re-establish
  // the rounded path first: the fill/texture passes above left their own paths.
  roundRectPath(ctx, rect, radius);
  ctx.lineWidth = selected ? 2 : 1;
  ctx.strokeStyle = selected
    ? palette.accent
    : hovered
      ? palette.nodeBorderHover
      : palette.nodeBorder;
  ctx.stroke();

  // Role status dot, top-right. One dot, most-salient role only.
  const role = rect.w >= DOT_MIN_PX && rect.h >= 28 ? roleColor(node, palette) : null;
  const roleDot = role !== null;
  if (role) {
    ctx.beginPath();
    ctx.arc(rect.x + rect.w - 13, rect.y + 13, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = role;
    ctx.fill();
  }

  // The node's own text recedes faster than its frame so that, mid-zoom, the
  // children fading in inside it read cleanly instead of overlapping its label.
  const textAlpha = alpha * Math.max(0, 1 - 2 * t);
  if (textAlpha > 0.02) drawCardText(ctx, rect, node, palette, textAlpha, roleDot, state.preview ?? null);
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
  lineWidth = EDGE_LINE_PX * couplingWidth(coupling),
): void {
  if (alpha <= 0.02) return;
  const { start, c1, c2, end, endAngle } = route;
  const color = coupling === "tight" ? palette.edgeStrong : palette.edge;
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
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
