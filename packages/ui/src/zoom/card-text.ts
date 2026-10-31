/**
 * Card text drawing: header, chip row and footer around the body. Browser code.
 *
 * `card-content.ts` decides what a card shows at its on-screen size; this
 * module paints it top to bottom with a y cursor, so each zone takes only the
 * room the previous ones left. Measurement lives in `card-measure.ts` and the
 * body in `card-body.ts`.
 */

import type { Rect } from "./camera";
import {
  NO_CHIPS,
  cardBadge,
  cardPadPx,
  cardTechnologies,
  cardTier,
  primaryMetric,
  titleFontPx,
  titleMode,
  type CardTier,
} from "./card-content";
import { bodyType, drawBody, healthColor, type CardFrame } from "./card-body";
import { font, textWidth, wrap } from "./card-measure";
import type { CardPreview } from "./card-preview";
import { drawKindGlyph } from "./kind-glyph";
import { KIND_LABEL } from "./node-signals";
import type { ZoomPalette } from "./theme";
import type { ZoomNode } from "./types";

const GLYPH_MIN_PX = 52; // draw the kind glyph beside the title once there is room
/** Room kept clear at the title's right edge for the role dot. */
const DOT_RESERVE_PX = 14;

// One frame object reused for every card: the painters run synchronously, so
// nothing holds it across cards and a frame allocates no per-card state.
const frame: CardFrame = {
  ctx: undefined as unknown as CanvasRenderingContext2D,
  rect: { x: 0, y: 0, w: 0, h: 0 },
  node: undefined as unknown as ZoomNode,
  palette: undefined as unknown as ZoomPalette,
  alpha: 1,
  pad: 0,
  titlePx: 0,
};

/** Glyph + name (up to two lines). Returns the y just below the title. */
function drawHeader(f: CardFrame, roleDot: boolean): number {
  const { ctx, rect, node, palette, pad, titlePx } = f;
  const lineH = Math.round(titlePx * 1.25);
  // On a card too short for pad + line + pad, centre the one line instead.
  const top = Math.round(rect.y + Math.min(pad, Math.max(0, (rect.h - lineH) / 2)));
  let titleX = Math.round(rect.x + pad);
  // A tiny file card spends its width on the name: the extension already says
  // it is a file, and the glyph would cost it five characters.
  const glyph = node.kind !== "file" || cardTier(rect.w, rect.h) !== "tiny";
  if (glyph && rect.w >= GLYPH_MIN_PX && rect.h >= 28) {
    // save/restore so the round joins and caps do not leak into edge strokes.
    ctx.save();
    ctx.globalAlpha = f.alpha * 0.85;
    ctx.strokeStyle = palette.nodeText;
    ctx.lineWidth = Math.max(1, titlePx * 0.1);
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    drawKindGlyph(ctx, titleX, top + (lineH - titlePx) / 2, titlePx, node.kind);
    ctx.restore();
    titleX += titlePx + Math.round(Math.max(5, titlePx * 0.4));
  }
  const maxW = rect.x + rect.w - pad - titleX - (roleDot ? DOT_RESERVE_PX : 0);
  const maxLines = rect.h - 2 * pad >= 2 * lineH ? 2 : 1;
  const lines = wrap(ctx, 600, titlePx, node.name, maxW, maxLines, titleMode(node.kind, node.name));
  ctx.font = font(600, titlePx);
  ctx.globalAlpha = f.alpha;
  ctx.fillStyle = palette.nodeText;
  for (let i = 0; i < lines.length; i++) ctx.fillText(lines[i]!, titleX, top + i * lineH);
  return top + Math.max(1, lines.length) * lineH;
}

/** Height of the chip row, including its gap above, for a title size. */
function chipRowPx(titlePx: number): number {
  return Math.max(9, Math.min(11, titlePx - 3)) + 10;
}

/**
 * Technology chips under the title at `y`, left to right until the row is
 * full. Returns how many were drawn (0 leaves the row unused).
 */
function drawChips(f: CardFrame, y: number, chips: readonly string[]): number {
  const { ctx, rect, palette, pad } = f;
  const px = Math.max(9, Math.min(11, f.titlePx - 3));
  const h = px + 6;
  const top = y + 4;
  let drawn = 0;
  ctx.lineWidth = 1;
  let x = Math.round(rect.x + pad);
  const right = rect.x + rect.w - pad;
  for (const chip of chips) {
    const w = Math.ceil(textWidth(ctx, 600, px, chip)) + 10;
    if (x + w > right) break;
    ctx.font = font(600, px);
    ctx.globalAlpha = f.alpha;
    ctx.strokeStyle = palette.nodeBorderHover;
    ctx.beginPath();
    ctx.roundRect(x + 0.5, top + 0.5, w - 1, h - 1, h / 2);
    ctx.stroke();
    ctx.fillStyle = palette.textSecondary;
    ctx.fillText(chip, x + 5, top + 3);
    x += w + 4;
    drawn++;
  }
  return drawn;
}

/** Footer px for a title size; also the footer's height in the zone budget. */
function footerPx(titlePx: number): number {
  return Math.max(10, Math.min(11, titlePx - 2));
}

/** Health dot + kind (or badge) left, one metric right, on the bottom edge. */
function drawFooter(f: CardFrame, chipsShown: boolean): void {
  const { ctx, rect, node, palette, pad } = f;
  const px = footerPx(f.titlePx);
  const baseY = Math.round(rect.y + rect.h - pad);
  let x = Math.round(rect.x + pad);
  // A small health dot leads the footer, colored on the same bands as the
  // /files treemap. Only drawn when the node has a score, so unscored files
  // (health is sparse) stay quiet rather than showing a neutral dot everywhere.
  if (node.health_score !== null) {
    const r = 3;
    ctx.globalAlpha = f.alpha * 0.9;
    ctx.fillStyle = healthColor(node.health_score, palette);
    ctx.beginPath();
    ctx.arc(x + r, baseY - px * 0.34, r, 0, Math.PI * 2);
    ctx.fill();
    x += 2 * r + Math.round(Math.max(5, px * 0.5));
  }
  ctx.font = font(600, px);
  ctx.textBaseline = "bottom";
  ctx.globalAlpha = f.alpha * 0.62;
  ctx.fillStyle = palette.textMuted;
  const right = rect.x + rect.w - pad;
  const kind = (cardBadge(node) ?? KIND_LABEL[node.kind]).toUpperCase();
  const label = wrap(ctx, 600, px, kind, right - x, 1, "words")[0] ?? "";
  const labelW = textWidth(ctx, 600, px, label);
  ctx.font = font(600, px);
  ctx.fillText(label, x, baseY);
  const metric = primaryMetric(node, chipsShown);
  // The metric takes what the label leaves; it is dropped rather than overlap.
  const metricW = right - x - labelW - 8;
  if (metric && metricW > 0) {
    ctx.textAlign = "right";
    ctx.globalAlpha = f.alpha * 0.82;
    ctx.fillStyle = palette.nodeText;
    const fitted = wrap(ctx, 600, px, metric, metricW, 1, "words");
    ctx.font = font(600, px);
    if (fitted[0]) ctx.fillText(fitted[0], Math.round(right), baseY);
    ctx.textAlign = "left";
  }
  ctx.textBaseline = "top";
}

/** Body gap under the chips (or title) and above the footer. */
const BODY_GAP_PX = 6;

/**
 * The chips to draw under the title at `y`: none on a small card, none that
 * would crowd the footer, and none on a short card where they would push out
 * its one summary line (what a box does reads better than what it is built
 * with, which the hover card and the sheet still give).
 */
function chipsFor(f: CardFrame, tier: CardTier, y: number, footerTop: number): readonly string[] {
  const chips = tier === "small" ? NO_CHIPS : cardTechnologies(f.node);
  const row = chipRowPx(f.titlePx);
  if (chips.length === 0 || y + row + 2 > footerTop) return NO_CHIPS;
  const { px } = bodyType(f);
  const room = (top: number): number => footerTop - top - 2 * BODY_GAP_PX;
  return f.node.summary && room(y + row) < px && room(y) >= px ? NO_CHIPS : chips;
}

/**
 * Paint a card's text zones at `alpha`, disclosing detail by on-screen size
 * (see `cardTier`). `roleDot` says whether the top-right role dot is drawn,
 * so the title keeps clear of it only when it is there; `preview` is a
 * container's "contains" list (see `card-preview.ts`). Leaves `textAlign`
 * left, `textBaseline` top and `globalAlpha` 1.
 */
export function drawCardText(
  ctx: CanvasRenderingContext2D,
  rect: Rect,
  node: ZoomNode,
  palette: ZoomPalette,
  alpha: number,
  roleDot: boolean,
  preview: CardPreview | null = null,
): void {
  const tier = cardTier(rect.w, rect.h);
  if (tier === "hidden") return;
  const f = frame;
  f.ctx = ctx;
  f.rect = rect;
  f.node = node;
  f.palette = palette;
  f.alpha = alpha;
  f.pad = cardPadPx(rect.w);
  f.titlePx = titleFontPx(rect.h);
  ctx.textBaseline = "top";
  ctx.textAlign = "left";

  let y = drawHeader(f, roleDot);
  const footerTop = rect.y + rect.h - f.pad - footerPx(f.titlePx);
  if (tier !== "tiny" && footerTop >= y + 2) {
    const chips = chipsFor(f, tier, y, footerTop);
    const chipsShown = chips.length > 0 && drawChips(f, y, chips) > 0;
    if (chipsShown) y += chipRowPx(f.titlePx);
    drawBody(f, tier, y + BODY_GAP_PX, footerTop - BODY_GAP_PX, preview);
    drawFooter(f, chipsShown);
  }
  ctx.globalAlpha = 1;
}
