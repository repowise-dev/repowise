/**
 * Minimap geometry. Pure, browser-free and unit-testable.
 *
 * The minimap draws the root box (see `rootRect`) into a `w` x `h` px box of
 * the same shape, inset by `pad`, its longer side `size` px. World maps
 * linearly and uniformly onto `[pad, w - pad] x [pad, h - pad]`.
 */

import type { Camera, Rect, Viewport, WorldPoint } from "./camera";

/** Smallest viewport marker, so a deep zoom still shows where you are. */
export const MINIMAP_MIN_MARK_PX = 4;

export interface MinimapFrame {
  /** Minimap width and height in css px. */
  w: number;
  h: number;
  pad: number;
  /** The world rect the minimap shows. */
  bounds: Rect;
  /** Minimap px per world unit. */
  scale: number;
}

/** A minimap of `bounds`' shape whose longer side is `size` px. */
export function minimapFrame(bounds: Rect, size: number, pad: number): MinimapFrame {
  const scale = Math.max(1e-9, size - 2 * pad) / Math.max(bounds.w, bounds.h, 1e-12);
  return {
    w: Math.round(2 * pad + bounds.w * scale),
    h: Math.round(2 * pad + bounds.h * scale),
    pad,
    bounds,
    scale,
  };
}

/** Minimap pixel -> world point. */
export function minimapToWorld(px: number, py: number, f: MinimapFrame): WorldPoint {
  return { wx: f.bounds.x + (px - f.pad) / f.scale, wy: f.bounds.y + (py - f.pad) / f.scale };
}

/** World rect -> minimap pixel rect (unclipped). */
export function worldToMinimapRect(rect: Rect, f: MinimapFrame): Rect {
  return {
    x: f.pad + (rect.x - f.bounds.x) * f.scale,
    y: f.pad + (rect.y - f.bounds.y) * f.scale,
    w: rect.w * f.scale,
    h: rect.h * f.scale,
  };
}

/**
 * The camera's view as a minimap rect, clipped to the minimap and grown about
 * its centre to at least `MINIMAP_MIN_MARK_PX` on each side. Writes into `out`
 * (and returns it) because it runs on every camera frame.
 */
export function minimapViewRect(
  cam: Camera,
  vp: Viewport,
  f: MinimapFrame,
  out: Rect = { x: 0, y: 0, w: 0, h: 0 },
): Rect {
  const w = (vp.w / cam.scale) * f.scale;
  const h = (vp.h / cam.scale) * f.scale;
  const x0 = f.pad + (cam.cx - f.bounds.x) * f.scale - w / 2;
  const y0 = f.pad + (cam.cy - f.bounds.y) * f.scale - h / 2;
  out.x = clipStart(x0, w, f.w);
  out.w = clipEnd(x0, w, f.w, out.x);
  out.y = clipStart(y0, h, f.h);
  out.h = clipEnd(y0, h, f.h, out.y);
  return out;
}

/** Clipped start of a `[start, start + len]` span on `[0, size]`, before growth. */
function clipStart(start: number, len: number, size: number): number {
  const a = Math.max(0, start);
  const b = Math.min(size, start + len);
  if (b - a >= MINIMAP_MIN_MARK_PX) return a;
  const mid = Math.min(size, Math.max(0, (a + b) / 2));
  return Math.max(0, Math.min(size - MINIMAP_MIN_MARK_PX, mid - MINIMAP_MIN_MARK_PX / 2));
}

/** Clipped length of the same span, given its clipped start. */
function clipEnd(start: number, len: number, size: number, clippedStart: number): number {
  const b = Math.min(size, start + len);
  return Math.max(MINIMAP_MIN_MARK_PX, b - clippedStart);
}
