/**
 * Pure 2D camera for the zoom canvas. Browser-free and fully unit-testable.
 *
 * The camera is **center-anchored**: it stores the world point shown at the
 * viewport centre (`cx`, `cy`) plus a `scale` (screen pixels per world unit).
 * This is itself the float-precision "rebase": because the visible centre is
 * always inside the root's `[0,1]` box, `cx`/`cy` stay `O(1)` no matter how far
 * you zoom in, so the world->screen subtraction `(wx - cx)` is between two
 * nearby `O(1)` doubles and keeps its relative precision. Only `scale` grows,
 * and a single float carries it exactly enough for any realistic depth.
 *
 * World space: the system root occupies the unit square `[0,1] x [0,1]`. A
 * node's absolute world rect is its layout rect composed down the tree (see
 * `geometry.ts`).
 */

import { MAX_SCALE, MIN_SCALE } from "./constants";

export interface Camera {
  /** World x shown at the viewport centre. */
  cx: number;
  /** World y shown at the viewport centre. */
  cy: number;
  /** Screen pixels per world unit. */
  scale: number;
}

export interface Viewport {
  w: number;
  h: number;
  /**
   * Screen px a host overlay covers on the right edge (a detail sheet). The
   * canvas still draws under it, but framing, fitting and "which node am I
   * in" all measure the clear part to its left, so nothing frames under the
   * sheet. 0 or unset when nothing covers the canvas.
   */
  insetRight?: number;
}

export interface ScreenPoint {
  sx: number;
  sy: number;
}

export interface WorldPoint {
  wx: number;
  wy: number;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

/** World coordinate -> screen pixel. */
export function worldToScreen(cam: Camera, vp: Viewport, wx: number, wy: number): ScreenPoint {
  return {
    sx: vp.w / 2 + (wx - cam.cx) * cam.scale,
    sy: vp.h / 2 + (wy - cam.cy) * cam.scale,
  };
}

/** Screen pixel -> world coordinate. */
export function screenToWorld(cam: Camera, vp: Viewport, sx: number, sy: number): WorldPoint {
  return {
    wx: cam.cx + (sx - vp.w / 2) / cam.scale,
    wy: cam.cy + (sy - vp.h / 2) / cam.scale,
  };
}

/** Width of the part of the viewport no overlay covers (see `Viewport.insetRight`). */
export function clearWidth(vp: Viewport): number {
  return Math.max(1, vp.w - (vp.insetRight ?? 0));
}

/** The world point at the centre of the clear part of the viewport. */
export function clearCentre(cam: Camera, vp: Viewport): WorldPoint {
  return { wx: cam.cx - (vp.w - clearWidth(vp)) / 2 / cam.scale, wy: cam.cy };
}

/**
 * Map an absolute world rect to a screen rect, written into `out` when given
 * (the draw pass reuses scratch rects so a frame allocates none).
 */
export function worldRectToScreen(
  cam: Camera,
  vp: Viewport,
  rect: Rect,
  out: Rect = { x: 0, y: 0, w: 0, h: 0 },
): Rect {
  out.x = vp.w / 2 + (rect.x - cam.cx) * cam.scale;
  out.y = vp.h / 2 + (rect.y - cam.cy) * cam.scale;
  out.w = rect.w * cam.scale;
  out.h = rect.h * cam.scale;
  return out;
}

/**
 * Zoom by `factor` about a fixed screen anchor (the world point under the
 * anchor stays put). `factor > 1` zooms in.
 */
export function zoomAbout(
  cam: Camera,
  vp: Viewport,
  anchorSx: number,
  anchorSy: number,
  factor: number,
): Camera {
  const anchor = screenToWorld(cam, vp, anchorSx, anchorSy);
  const scale = clampScale(cam.scale * factor);
  // Solve for the centre that keeps `anchor` under (anchorSx, anchorSy):
  //   anchorSx = vp.w/2 + (anchor.wx - cx') * scale
  return {
    cx: anchor.wx - (anchorSx - vp.w / 2) / scale,
    cy: anchor.wy - (anchorSy - vp.h / 2) / scale,
    scale,
  };
}

/** Line height a line-mode wheel (`deltaMode` 1, Firefox) scrolls by. */
const WHEEL_LINE_PX = 16;

/**
 * A wheel event's vertical scroll in px, whatever unit the device reported it
 * in: pixels, lines, or pages of `pageHeight` px.
 */
export function wheelPixels(e: { deltaY: number; deltaMode: number }, pageHeight: number): number {
  if (e.deltaMode === 1) return e.deltaY * WHEEL_LINE_PX;
  if (e.deltaMode === 2) return e.deltaY * pageHeight;
  return e.deltaY;
}

/** Pan by a screen-pixel delta (dragging the world with the cursor). */
export function panByScreen(cam: Camera, dxScreen: number, dyScreen: number): Camera {
  return {
    cx: cam.cx - dxScreen / cam.scale,
    cy: cam.cy - dyScreen / cam.scale,
    scale: cam.scale,
  };
}

/**
 * Keep the viewport centre within the root box (with a small margin) so the
 * user cannot lose the graph off-screen. Pure: returns a corrected camera.
 */
export function clampCamera(cam: Camera, margin = 0.25): Camera {
  return {
    cx: Math.min(1 + margin, Math.max(-margin, cam.cx)),
    cy: Math.min(1 + margin, Math.max(-margin, cam.cy)),
    scale: clampScale(cam.scale),
  };
}

/** The whole world: the unit square the root box sits in. */
export const UNIT_RECT: Rect = { x: 0, y: 0, w: 1, h: 1 };

/**
 * A camera that fits the root box (see `rootRect`; the unit square when not
 * given) into the viewport with padding, so the whole overview is visible.
 */
export function fitRoot(vp: Viewport, root: Rect = UNIT_RECT, padding = 0.9): Camera {
  return frameRect(vp, root, padding);
}

/** The scale `fitRoot` uses, without allocating a camera. */
export function fitScale(vp: Viewport, root: Rect = UNIT_RECT, padding = 0.9): number {
  return clampScale(
    padding * Math.min(clearWidth(vp) / Math.max(root.w, 1e-12), vp.h / Math.max(root.h, 1e-12)),
  );
}

/**
 * A camera that frames an absolute world rect at `fill` of a true fit, centred
 * in the clear part of the viewport: the rect's binding axis spans `fill` of
 * that area along that axis. Used by "zoom to node" interactions.
 * `viewportCoverage` of the framed rect is exactly `fill`, which is what lets
 * `focusChain` agree with a dive.
 */
export function frameRect(vp: Viewport, rect: Rect, fill = 0.8): Camera {
  const fit = Math.min(clearWidth(vp) / Math.max(rect.w, 1e-12), vp.h / Math.max(rect.h, 1e-12));
  const scale = clampScale(fill * fit);
  // Shift the centre right by half the covered strip, so the rect sits mid-clear.
  const shift = (vp.w - clearWidth(vp)) / 2 / scale;
  return { cx: rect.x + rect.w / 2 + shift, cy: rect.y + rect.h / 2, scale };
}

/**
 * How much of the clear viewport a world rect fills along its binding axis: 1
 * means it exactly fits, 0.5 half fits. The one measure both "frame this node"
 * and "which node am I in" read, so they cannot disagree on viewport shape.
 */
export function viewportCoverage(cam: Camera, vp: Viewport, rect: Rect): number {
  return Math.max((rect.w * cam.scale) / clearWidth(vp), (rect.h * cam.scale) / vp.h);
}

/**
 * The camera to keep after the viewport resizes. Centre and scale are kept, so
 * the map does not rescale under the pointer; the one exception is a camera
 * still sitting on the whole-system fit, which refits so the overview stays
 * whole when the window or sidebar changes.
 */
export function cameraAfterResize(
  cam: Camera,
  prev: Viewport,
  next: Viewport,
  root: Rect = UNIT_RECT,
): Camera {
  const fit = fitRoot(prev, root);
  const atFit =
    Math.abs(cam.cx - fit.cx) < 1e-9 &&
    Math.abs(cam.cy - fit.cy) < 1e-9 &&
    Math.abs(cam.scale / fit.scale - 1) < 1e-9;
  return atFit ? fitRoot(next, root) : cam;
}

/**
 * The camera that shows a node the same way after a re-layout moved it from
 * `from` to `to` (both world rects): the same point of the node stays at the
 * viewport centre and the node keeps its on-screen size. Cards keep their
 * shape across a re-layout; the root may not (it follows the canvas), so the
 * size kept is the area's, which for an unchanged shape is the width.
 */
export function cameraAcrossRelayout(cam: Camera, from: Rect, to: Rect): Camera {
  if (from.w <= 0 || from.h <= 0) return cam;
  const k = Math.sqrt((to.w * to.h) / (from.w * from.h));
  return {
    cx: to.x + ((cam.cx - from.x) / from.w) * to.w,
    cy: to.y + ((cam.cy - from.y) / from.h) * to.h,
    scale: clampScale(cam.scale / k),
  };
}

/**
 * Pan (never zoom) the least distance that brings a world rect inside the
 * clear viewport: every edge, minus a strip of `rightInset` screen px the host
 * covers on the right, keeping `margin` px of air. A rect bigger than the clear
 * area along an axis aligns its left (or top) edge instead. Returns `cam`
 * itself when the rect is already clear.
 */
export function panToReveal(
  cam: Camera,
  vp: Viewport,
  rect: Rect,
  rightInset: number,
  margin = 16,
): Camera {
  const s = worldRectToScreen(cam, vp, rect);
  const dx = revealShift(s.x, s.w, margin, vp.w - rightInset - margin);
  const dy = revealShift(s.y, s.h, margin, vp.h - margin);
  if (dx === 0 && dy === 0) return cam;
  return panByScreen(cam, dx, dy);
}

/** The shift that moves `[start, start + size]` inside `[lo, hi]`, leading edge first. */
function revealShift(start: number, size: number, lo: number, hi: number): number {
  let d = 0;
  if (start + size > hi) d = hi - (start + size);
  if (start + d < lo) d = lo - start;
  return d;
}
