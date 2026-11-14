/**
 * Card text drawing: header, technology line and footer around the body.
 * Browser code.
 *
 * `card-content.ts` decides what a card shows at its on-screen size; this
 * module paints it top to bottom with a y cursor, so each zone takes only the
 * room the previous ones left. Measurement lives in `card-measure.ts` and the
 * body in `card-body.ts`.
 */

import type { Rect } from "./camera";
import {
  NO_TECH,
  cardPadPx,
  cardRoleMarks,
  cardScore,
  cardTechPrefixes,
  cardTier,
  primaryMetric,
  titleFontPx,
  titleMode,
  type CardRoleMark,
  type CardTier,
} from "./card-content";
import { bodyType, drawBody, healthColor, type CardFrame } from "./card-body";
import { font, textWidth, wrap } from "./card-measure";
import type { CardPreview } from "./card-preview";
import { drawKindGlyph, drawRoleGlyph } from "./kind-glyph";
import type { ZoomPalette } from "./theme";
import type { ZoomNode } from "./types";

const GLYPH_MIN_PX = 52; // draw the kind glyph beside the title once there is room
/** Draw the corner health score once the card has room beside the name for it. */
const SCORE_MIN_W_PX = 60;
const SCORE_MIN_H_PX = 28;
/** Gap between the name and the corner score. */
const SCORE_GAP_PX = 6;
/** Gap before each role icon beside a file's name. */
const MARK_GAP_PX = 4;
/** Footer spacing: icon to its count, and one role to the next. */
const ICON_GAP_PX = 3;
const ROLE_GAP_PX = 10;

const NO_MARKS: readonly CardRoleMark[] = [];

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

function scorePx(titlePx: number): number {
  return Math.max(10, Math.round(titlePx * 0.82));
}

/**
 * Room the corner score takes from the name, gap included, or 0 when it is
 * not drawn: an unscored node, a card too small for it, or a tiny card whose
 * name would be cut by it (a tiny card spends its width on the name, as it
 * does with the kind glyph).
 */
function scoreRoom(f: CardFrame, score: string | null, nameRoom: number): number {
  const { ctx, rect, node, titlePx } = f;
  if (score === null || rect.w < SCORE_MIN_W_PX || rect.h < SCORE_MIN_H_PX) return 0;
  const room = textWidth(ctx, 700, scorePx(titlePx), score) + SCORE_GAP_PX;
  const tiny = cardTier(rect.w, rect.h) === "tiny";
  return tiny && textWidth(ctx, 600, titlePx, node.name) > nameRoom - room ? 0 : room;
}

/** The health score, bold and in its band colour, right-aligned at the title's height. */
function drawScore(f: CardFrame, score: string, top: number, right: number): void {
  const { ctx, node, palette, titlePx } = f;
  const px = scorePx(titlePx);
  ctx.font = font(700, px);
  ctx.textAlign = "right";
  ctx.globalAlpha = f.alpha;
  ctx.fillStyle = healthColor(node.health_score, palette);
  ctx.fillText(score, Math.round(right), Math.round(top + (titlePx - px) / 2));
  ctx.textAlign = "left";
}

/**
 * Glyph + name (up to two lines) + corner score; a file's role icons follow
 * its name. Returns the y just below the title.
 */
function drawHeader(f: CardFrame): number {
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
  // A file marks its own roles beside its name; a container counts them in its footer.
  const marks = node.kind === "file" && rect.w >= GLYPH_MIN_PX ? cardRoleMarks(node) : NO_MARKS;
  const markPx = Math.round(titlePx * 0.85);
  const right = rect.x + rect.w - pad;
  const nameRoom = right - titleX - marks.length * (markPx + MARK_GAP_PX);
  const score = cardScore(node);
  const scoreW = scoreRoom(f, score, nameRoom);
  if (score !== null && scoreW > 0) drawScore(f, score, top, right);
  const maxW = nameRoom - scoreW;
  const maxLines = rect.h - 2 * pad >= 2 * lineH ? 2 : 1;
  const lines = wrap(ctx, 600, titlePx, node.name, maxW, maxLines, titleMode(node.kind, node.name));
  ctx.font = font(600, titlePx);
  ctx.globalAlpha = f.alpha;
  ctx.fillStyle = palette.nodeText;
  for (let i = 0; i < lines.length; i++) ctx.fillText(lines[i]!, titleX, top + i * lineH);
  const last = lines[lines.length - 1];
  if (marks.length > 0 && last) {
    let x = titleX + textWidth(ctx, 600, titlePx, last) + MARK_GAP_PX;
    const y = top + (lines.length - 1) * lineH + (titlePx - markPx) / 2;
    for (const m of marks) {
      drawRoleGlyph(ctx, x, y, markPx, m.role, palette);
      x += markPx + MARK_GAP_PX;
    }
  }
  return top + Math.max(1, lines.length) * lineH;
}

/** Technology line type size for a title size. */
function techPx(titlePx: number): number {
  return Math.max(10, Math.min(12, titlePx - 3));
}

/** Height of the technology line, including its gap above. */
function techRowPx(titlePx: number): number {
  return techPx(titlePx) + 8;
}

/**
 * The technology line under the title at `y`: muted text, the longest run of
 * whole technologies that fits. False when not even one fits.
 */
function drawTech(f: CardFrame, y: number, prefixes: readonly string[]): boolean {
  const { ctx, rect, palette, pad } = f;
  const px = techPx(f.titlePx);
  const maxW = rect.w - 2 * pad;
  let row = "";
  for (const p of prefixes) {
    if (textWidth(ctx, 400, px, p) > maxW) break;
    row = p;
  }
  if (!row) return false;
  ctx.font = font(400, px);
  ctx.globalAlpha = f.alpha;
  ctx.fillStyle = palette.textMuted;
  ctx.fillText(row, Math.round(rect.x + pad), y + 4);
  return true;
}

/** Footer px for a title size; also the footer's height in the zone budget. */
function footerPx(titlePx: number): number {
  return Math.max(10, Math.min(11, titlePx - 2));
}

/**
 * A container's footer on the bottom edge: its non-zero role counts with
 * their icons left, the file count right. A role that would run into the
 * file count is dropped whole.
 */
function drawFooter(f: CardFrame): void {
  const { ctx, rect, node, palette, pad } = f;
  const px = footerPx(f.titlePx);
  const baseY = Math.round(rect.y + rect.h - pad);
  const left = Math.round(rect.x + pad);
  const right = rect.x + rect.w - pad;
  ctx.textBaseline = "bottom";
  let limit = right;
  const metric = primaryMetric(node);
  const fitted = metric ? wrap(ctx, 600, px, metric, right - left, 1, "words")[0] : undefined;
  if (fitted) {
    ctx.font = font(600, px);
    ctx.textAlign = "right";
    ctx.globalAlpha = f.alpha * 0.82;
    ctx.fillStyle = palette.nodeText;
    ctx.fillText(fitted, Math.round(right), baseY);
    ctx.textAlign = "left";
    limit = right - textWidth(ctx, 600, px, fitted) - ROLE_GAP_PX;
  }
  const s = px + 1;
  const iconY = baseY - (px + s) / 2 - 1;
  let x = left;
  for (const m of cardRoleMarks(node)) {
    const countW = textWidth(ctx, 600, px, m.count);
    if (x + s + ICON_GAP_PX + countW > limit) break;
    ctx.globalAlpha = f.alpha;
    drawRoleGlyph(ctx, x, iconY, s, m.role, palette);
    ctx.font = font(600, px);
    ctx.globalAlpha = f.alpha * 0.82;
    ctx.fillStyle = palette.nodeText;
    ctx.fillText(m.count, x + s + ICON_GAP_PX, baseY);
    x += s + ICON_GAP_PX + countW + ROLE_GAP_PX;
  }
  ctx.textBaseline = "top";
}

/** Body gap under the technologies (or title) and above the footer. */
const BODY_GAP_PX = 6;

/**
 * The technology line to draw under the title at `y`: none on a small card,
 * none that would crowd the footer, and none on a short card where it would
 * push out its one summary line (what a box does reads better than what it is
 * built with, which the hover card and the sheet still give).
 */
function techFor(f: CardFrame, tier: CardTier, y: number, footerTop: number): readonly string[] {
  const tech = tier === "small" ? NO_TECH : cardTechPrefixes(f.node);
  const row = techRowPx(f.titlePx);
  if (tech.length === 0 || y + row + 2 > footerTop) return NO_TECH;
  const { px } = bodyType(f);
  const room = (top: number): number => footerTop - top - 2 * BODY_GAP_PX;
  return f.node.summary && room(y + row) < px && room(y) >= px ? NO_TECH : tech;
}

/**
 * Paint a card's text zones at `alpha`, disclosing detail by on-screen size
 * (see `cardTier`). `preview` is a container's "contains" list and health
 * composition (see `card-preview.ts`). A file has no footer: its roles sit
 * beside its name and its score in the corner. Leaves `textAlign` left,
 * `textBaseline` top and `globalAlpha` 1.
 */
export function drawCardText(
  ctx: CanvasRenderingContext2D,
  rect: Rect,
  node: ZoomNode,
  palette: ZoomPalette,
  alpha: number,
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

  let y = drawHeader(f);
  const footer = node.kind !== "file";
  const footerTop = rect.y + rect.h - f.pad - (footer ? footerPx(f.titlePx) : 0);
  if (tier !== "tiny" && footerTop >= y + 2) {
    const tech = techFor(f, tier, y, footerTop);
    if (tech.length > 0 && drawTech(f, y, tech)) y += techRowPx(f.titlePx);
    drawBody(f, tier, y + BODY_GAP_PX, footerTop - (footer ? BODY_GAP_PX : 0), preview);
    if (footer) drawFooter(f);
  }
  ctx.globalAlpha = 1;
}
