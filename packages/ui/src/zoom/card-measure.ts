/**
 * Card text measurement: a shared wrap cache in reference-size units and
 * interned font strings, so neither a steady frame nor a zooming one measures
 * text or builds a font string. Browser code.
 */

import { TextLayoutCache, type WrapMode } from "./text";

/** Shared by every card on every canvas; bounded, see `TextLayoutCache`. */
export const cardTextCache = new TextLayoutCache();

const FONT_STACK = "ui-sans-serif, system-ui, sans-serif";
const fonts = new Map<number, string>();

export type Weight = 400 | 600 | 700;

/** Interned CSS font string for a weight and whole-px size. */
export function font(weight: Weight, px: number): string {
  const key = weight * 1000 + px;
  let f = fonts.get(key);
  if (!f) fonts.set(key, (f = `${weight} ${px}px ${FONT_STACK}`));
  return f;
}

/**
 * Text is measured and wrapped once at this reference size and scaled, since
 * a glyph's advance is linear in font size. A zoom then changes px and width
 * together, so the cached wrap (keyed in reference units) keeps hitting and a
 * zooming frame measures nothing. `FIT_SLACK` absorbs the small-size hinting
 * that can make real text a hair wider than the scaled estimate.
 */
const REF_PX = 100;
const FIT_SLACK = 0.97;

export function wrap(
  ctx: CanvasRenderingContext2D,
  weight: Weight,
  px: number,
  text: string,
  maxW: number,
  maxLines: number,
  mode: WrapMode,
): readonly string[] {
  const ref = (maxW * REF_PX * FIT_SLACK) / px;
  return cardTextCache.lines(ctx, font(weight, REF_PX), text, ref, maxLines, mode);
}

export function textWidth(ctx: CanvasRenderingContext2D, weight: Weight, px: number, s: string): number {
  return (cardTextCache.width(ctx, font(weight, REF_PX), s) * px) / REF_PX;
}
