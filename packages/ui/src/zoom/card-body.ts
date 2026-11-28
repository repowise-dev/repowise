/**
 * Card body painting: the summary, the "contains" list and the composition
 * bar, plus the frame and health colors the other card zones share. Browser
 * code.
 */

import { bandForScore, type HealthBand } from "@repowise-dev/types/health";

import type { Rect } from "./camera";
import { summaryLineCap, type CardTier } from "./card-content";
import { font, textWidth, wrap } from "./card-measure";
import { COMPOSITION_BANDS, type CardPreview, type PreviewItem } from "./card-preview";
import type { ZoomPalette } from "./theme";
import type { ZoomNode } from "./types";

const HEALTH_SLOT: Record<HealthBand, keyof ZoomPalette> = {
  excellent: "healthGood",
  good: "healthGood",
  fair: "healthFair",
  needs_work: "healthNeedsWork",
  at_risk: "healthAtRisk",
};

/**
 * Ink for a node's code-health score, on the shared bands, so a card, the
 * detail panel and a treemap tile never disagree. Null (unscored) reads
 * neutral.
 */
export function healthColor(score: number | null, palette: ZoomPalette): string {
  return bandColor(score === null ? null : bandForScore(score), palette);
}

function bandColor(band: HealthBand | null, palette: ZoomPalette): string {
  return band === null ? palette.healthNeutral : palette[HEALTH_SLOT[band]];
}

/** Per-card sizes shared by the zone painters below. */
export interface CardFrame {
  ctx: CanvasRenderingContext2D;
  rect: Rect;
  node: ZoomNode;
  palette: ZoomPalette;
  alpha: number;
  pad: number;
  titlePx: number;
}

/** Summary lines kept when a "contains" list shares the body. */
const PREVIEW_SUMMARY_LINES = 3;
/** A preview column narrower than this would cut most names. */
const PREVIEW_COL_MIN_PX = 132;
const PREVIEW_COL_GAP_PX = 16;
/** Composition bar height (thinner on a small card) and the gap above it. */
const BAR_PX = 6;
const BAR_SMALL_PX = 4;
const BAR_GAP_PX = 8;

const moreLabels = new Map<number, string>();

/** Interned "+N more" so a frame builds no strings. */
function moreLabel(n: number): string {
  let s = moreLabels.get(n);
  if (!s) moreLabels.set(n, (s = `+${n} more`));
  return s;
}

/** One preview row: health dot, name, file count right-aligned. */
function drawPreviewItem(
  f: CardFrame,
  it: PreviewItem,
  x: number,
  y: number,
  w: number,
  px: number,
): void {
  const { ctx, palette } = f;
  const r = 2.5;
  ctx.globalAlpha = f.alpha * 0.9;
  ctx.fillStyle = bandColor(it.band, palette);
  ctx.beginPath();
  ctx.arc(x + r, y + px * 0.55, r, 0, Math.PI * 2);
  ctx.fill();
  const nameX = x + 2 * r + 6;
  const countW = it.count ? Math.ceil(textWidth(ctx, 400, px, it.count)) + 6 : 0;
  const name = wrap(ctx, 400, px, it.name, x + w - nameX - countW, 1, it.mode)[0];
  ctx.font = font(400, px);
  ctx.globalAlpha = f.alpha;
  ctx.fillStyle = palette.textSecondary;
  if (name) ctx.fillText(name, nameX, y);
  if (it.count) {
    ctx.textAlign = "right";
    ctx.globalAlpha = f.alpha * 0.8;
    ctx.fillStyle = palette.textMuted;
    ctx.fillText(it.count, Math.round(x + w), y);
    ctx.textAlign = "left";
  }
}

/**
 * The "contains" list between `top` and `bottom`: top children in one or two
 * columns (filled top to bottom), the last slot saying how many are not
 * shown. Skipped (false) when fewer than two names would show.
 */
function drawPreviewList(f: CardFrame, preview: CardPreview, top: number, bottom: number): boolean {
  const { ctx, rect, palette, pad } = f;
  const px = Math.max(11, Math.min(13, f.titlePx - 3));
  const rowH = Math.round(px * 1.5);
  const rows = Math.floor((bottom - top) / rowH);
  if (rows < Math.min(2, preview.total)) return false;
  const inner = rect.w - 2 * pad;
  const cols = inner >= 2 * PREVIEW_COL_MIN_PX + PREVIEW_COL_GAP_PX ? 2 : 1;
  const colW = (inner - (cols - 1) * PREVIEW_COL_GAP_PX) / cols;
  const slots = rows * cols;
  const shown = Math.min(preview.items.length, preview.total <= slots ? slots : slots - 1);
  // One listed name and a "+N more" says too little to be worth the room.
  if (shown < Math.min(2, preview.total)) return false;
  // Balance the columns: use only the rows the listed slots need.
  const used = Math.ceil((shown + (shown < preview.total ? 1 : 0)) / cols);
  const x0 = Math.round(rect.x + pad);
  // Column-major slots: fill the first column top to bottom, then the next.
  for (let i = 0; i < shown; i++) {
    const x = x0 + Math.floor(i / used) * (colW + PREVIEW_COL_GAP_PX);
    drawPreviewItem(f, preview.items[i]!, x, Math.round(top + (i % used) * rowH), colW, px);
  }
  if (shown < preview.total) {
    ctx.font = font(400, px);
    ctx.globalAlpha = f.alpha * 0.8;
    ctx.fillStyle = palette.textMuted;
    const x = x0 + Math.floor(shown / used) * (colW + PREVIEW_COL_GAP_PX);
    ctx.fillText(moreLabel(preview.total - shown), x, Math.round(top + (shown % used) * rowH));
  }
  return true;
}

/**
 * Files by health band as one thin stacked bar on the body's `bottom` edge,
 * worst band first, from the small tier up: it says how a box's health is
 * spread where the corner score gives only the average. Returns the bottom
 * left for the zones above it.
 */
function drawComposition(
  f: CardFrame,
  preview: CardPreview,
  tier: CardTier,
  top: number,
  bottom: number,
): number {
  const h = tier === "small" ? BAR_SMALL_PX : BAR_PX;
  if (preview.files <= 0 || bottom - top < h) return bottom;
  const { ctx, rect, palette, pad } = f;
  const y = bottom - h;
  const x0 = rect.x + pad;
  const w = rect.w - 2 * pad;
  ctx.save();
  ctx.beginPath();
  ctx.roundRect(x0, y, w, h, h / 2);
  ctx.clip();
  let x = x0;
  for (let i = 0; i < preview.bands.length; i++) {
    const n = preview.bands[i]!;
    if (n <= 0) continue;
    const seg = (n / preview.files) * w;
    const band = COMPOSITION_BANDS[i];
    // Unscored files read as a quiet track, not as a band.
    ctx.globalAlpha = f.alpha * (band ? 0.9 : 0.3);
    ctx.fillStyle = band ? palette[HEALTH_SLOT[band]] : palette.healthNeutral;
    ctx.fillRect(x, y, seg + 0.5, h);
    x += seg;
  }
  ctx.restore();
  return y - BAR_GAP_PX;
}

/** Summary lines from `y`, at most `cap`; returns the y below them. */
function drawSummary(f: CardFrame, px: number, lineH: number, y: number, cap: number): number {
  const { ctx, rect, node, palette, pad } = f;
  const lines = wrap(ctx, 400, px, node.summary, rect.w - 2 * pad, cap, "words");
  ctx.font = font(400, px);
  ctx.globalAlpha = f.alpha;
  ctx.fillStyle = palette.textSecondary;
  const x = Math.round(rect.x + pad);
  for (let i = 0; i < lines.length; i++) ctx.fillText(lines[i]!, x, y + i * lineH);
  return y + lines.length * lineH;
}

/** Text lines of `px` type at `lineH` spacing in `space`: the last needs only its glyph height. */
function linesThatFit(space: number, px: number, lineH: number): number {
  return space < px ? 0 : 1 + Math.floor((space - px) / lineH);
}

/**
 * The summary and the "contains" list from `top`. With a list to follow the
 * summary is kept short.
 */
function drawContent(
  f: CardFrame,
  tier: CardTier,
  listed: CardPreview | null,
  top: number,
  bottom: number,
): void {
  const { px, lineH } = bodyType(f);
  const tierCap = summaryLineCap(tier);
  const cap = listed ? Math.min(PREVIEW_SUMMARY_LINES, tierCap) : tierCap;
  const lines = f.node.summary ? Math.min(cap, linesThatFit(bottom - top, px, lineH)) : 0;
  const y = lines > 0 ? drawSummary(f, px, lineH, top, lines) + 6 : top;
  if (listed !== null) drawPreviewList(f, listed, y, bottom);
}

// Reused (read synchronously): body type sizes without a per-card object.
const bodyTypeOut = { px: 0, lineH: 0 };

/** Body text px and line height for the card's title size. */
export function bodyType(f: CardFrame): { px: number; lineH: number } {
  bodyTypeOut.px = Math.max(11, Math.min(14, Math.round(f.titlePx * 0.82)));
  bodyTypeOut.lineH = Math.round(bodyTypeOut.px * 1.4);
  return bodyTypeOut;
}

/**
 * The body between `top` and `bottom`: the composition bar anchored to the
 * bottom (small cards up), then, from medium up, the summary (capped by tier,
 * shorter when a list follows) and a container's "contains" list top down.
 */
export function drawBody(
  f: CardFrame,
  tier: CardTier,
  top: number,
  bottom: number,
  preview: CardPreview | null,
): void {
  if (preview) bottom = drawComposition(f, preview, tier, top, bottom);
  if (tier === "small" || bottom - top < bodyType(f).px) return;
  drawContent(f, tier, preview, top, bottom);
}
