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

import { ALERT_MAX, HEALTHY_MIN } from "@repowise-dev/types/health";

import type { Rect } from "./camera";
import { ARROW_SIZE_PX, EDGE_LINE_PX } from "./constants";
import type { EdgeRoute } from "./edges";
import type { ZoomPalette } from "./theme";
import type { ZoomKind, ZoomNode } from "./types";

const LABEL_MIN_PX = 44; // draw the name once a card is at least this wide
const SUMMARY_MIN_PX = 260; // draw a one-line summary on large cards
const DOT_MIN_PX = 60; // draw the role status dot once there is room
const CORNER_PX = 12;
const TEXTURE_MIN_PX = 96; // paint the paper texture once the card is big enough
const GLYPH_MIN_PX = 52; // draw the kind glyph beside the title once there is room
const FOOTER_MIN_W_PX = 132; // draw the bottom signal row on cards at least this wide
const FOOTER_MIN_H_PX = 104; // ...and at least this tall

/** Human label per node kind, shown small in the card footer. */
const KIND_LABEL: Record<ZoomKind, string> = {
  system: "System",
  layer: "Layer",
  group: "Group",
  folder: "Folder",
  file: "File",
};

/**
 * A minimal monoline glyph per node kind, drawn into the `[x, y, s, s]` box in the
 * caller's current stroke style. Canvas-native rather than the KG cards' Lucide
 * components (which cannot be drawn to a 2D context), but echoes the same visual
 * language: stacked planes = layer, folder = folder/group, a page = file, a grid
 * of boxes = the whole system.
 */
function drawKindGlyph(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  s: number,
  kind: ZoomKind,
): void {
  const at = (fx: number, fy: number): [number, number] => [x + fx * s, y + fy * s];
  ctx.beginPath();
  switch (kind) {
    case "system": {
      // 2x2 grid of small boxes.
      const d = 0.34 * s;
      const cells: Array<[number, number]> = [
        [0.1, 0.1],
        [0.56, 0.1],
        [0.1, 0.56],
        [0.56, 0.56],
      ];
      for (const [gx, gy] of cells) {
        ctx.rect(x + gx * s, y + gy * s, d, d);
      }
      break;
    }
    case "layer": {
      // Two stacked planes (Lucide "Layers" idiom).
      ctx.moveTo(...at(0.5, 0.1));
      ctx.lineTo(...at(0.88, 0.32));
      ctx.lineTo(...at(0.5, 0.54));
      ctx.lineTo(...at(0.12, 0.32));
      ctx.closePath();
      ctx.moveTo(...at(0.12, 0.52));
      ctx.lineTo(...at(0.5, 0.74));
      ctx.lineTo(...at(0.88, 0.52));
      break;
    }
    case "group": {
      // Two overlapping cards (a collection).
      ctx.rect(x + 0.34 * s, y + 0.14 * s, 0.44 * s, 0.44 * s);
      ctx.rect(x + 0.14 * s, y + 0.36 * s, 0.44 * s, 0.44 * s);
      break;
    }
    case "folder": {
      // Classic tabbed folder.
      ctx.moveTo(...at(0.14, 0.3));
      ctx.lineTo(...at(0.42, 0.3));
      ctx.lineTo(...at(0.5, 0.42));
      ctx.lineTo(...at(0.86, 0.42));
      ctx.lineTo(...at(0.86, 0.82));
      ctx.lineTo(...at(0.14, 0.82));
      ctx.closePath();
      break;
    }
    case "file": {
      // Page with a folded corner.
      ctx.moveTo(...at(0.24, 0.08));
      ctx.lineTo(...at(0.6, 0.08));
      ctx.lineTo(...at(0.78, 0.26));
      ctx.lineTo(...at(0.78, 0.92));
      ctx.lineTo(...at(0.24, 0.92));
      ctx.closePath();
      ctx.moveTo(...at(0.6, 0.08));
      ctx.lineTo(...at(0.6, 0.26));
      ctx.lineTo(...at(0.78, 0.26));
      break;
    }
  }
  ctx.stroke();
}

/**
 * The single most useful bottom-right signal for a node. Files show their
 * language; containers show how many files they hold. Code health is shown
 * separately as the footer's leading dot (see `healthColor`).
 */
function primaryMetric(node: ZoomNode): string {
  if (node.kind === "file") return node.language ? node.language.toUpperCase() : "";
  const n = node.metrics.file_count;
  return n > 0 ? `${n} ${n === 1 ? "file" : "files"}` : "";
}

/**
 * Traffic-light ink for a node's code-health score, on the same 0-10 bands the
 * /files treemap uses (`bandForScore`: <4 alert, <8 warning, else healthy) so a
 * card and a treemap tile never disagree. Null (unscored, sparse) reads neutral.
 */
function healthColor(score: number | null, palette: ZoomPalette): string {
  if (score === null) return palette.healthNeutral;
  if (score < ALERT_MAX) return palette.healthAlert;
  if (score < HEALTHY_MIN) return palette.healthWarning;
  return palette.healthHealthy;
}

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

  // Kind glyph before the title so layers / folders / files are distinguishable
  // at a glance (mirrors the KG cards' kind icon). The title shifts right to clear it.
  let titleX = rect.x + pad;
  if (rect.w >= GLYPH_MIN_PX && rect.h >= 28) {
    const gs = fontSize;
    ctx.save();
    ctx.globalAlpha = textAlpha * 0.85;
    ctx.strokeStyle = palette.nodeText;
    ctx.lineWidth = Math.max(1, fontSize * 0.1);
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    drawKindGlyph(ctx, rect.x + pad, rect.y + pad, gs, node.kind);
    ctx.restore();
    titleX += gs + Math.max(5, fontSize * 0.4);
  }
  // Leave room for the dot so a long name never collides with it.
  const labelMax = rect.x + rect.w - pad - titleX - (rect.w >= DOT_MIN_PX ? 14 : 0);
  const label = fitText(ctx, node.name, labelMax);
  ctx.fillText(label, titleX, rect.y + pad);

  if (rect.w >= SUMMARY_MIN_PX && rect.h >= 96 && node.summary) {
    ctx.fillStyle = palette.textMuted;
    ctx.font = `400 ${Math.max(11, fontSize - 3)}px ui-sans-serif, system-ui, sans-serif`;
    const summary = fitText(ctx, node.summary, rect.w - pad * 2);
    ctx.fillText(summary, rect.x + pad, rect.y + pad + fontSize + 6);
  }

  // Footer signal row (large cards): health dot + kind label left, primary metric right.
  if (rect.w >= FOOTER_MIN_W_PX && rect.h >= FOOTER_MIN_H_PX) {
    const fFont = Math.max(9, Math.min(11, fontSize - 4));
    const baseY = rect.y + rect.h - pad;
    let kindX = rect.x + pad;
    // A small health dot leads the footer, colored on the same bands as the
    // /files treemap. Only drawn when the node has a score, so unscored files
    // (health is sparse) stay quiet rather than showing a neutral dot everywhere.
    if (node.health_score !== null) {
      const r = 3;
      ctx.globalAlpha = textAlpha * 0.9;
      ctx.fillStyle = healthColor(node.health_score, palette);
      ctx.beginPath();
      ctx.arc(kindX + r, baseY - fFont * 0.34, r, 0, Math.PI * 2);
      ctx.fill();
      kindX += 2 * r + Math.max(5, fFont * 0.5);
    }
    ctx.font = `600 ${fFont}px ui-sans-serif, system-ui, sans-serif`;
    ctx.textBaseline = "bottom";
    ctx.textAlign = "left";
    ctx.globalAlpha = textAlpha * 0.62;
    ctx.fillStyle = palette.textMuted;
    ctx.fillText(KIND_LABEL[node.kind].toUpperCase(), kindX, baseY);
    const metric = primaryMetric(node);
    if (metric) {
      ctx.textAlign = "right";
      ctx.globalAlpha = textAlpha * 0.82;
      ctx.fillStyle = palette.nodeText;
      ctx.fillText(fitText(ctx, metric, rect.w * 0.5), rect.x + rect.w - pad, baseY);
    }
    // Reset the text state the rest of the renderer expects.
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
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
