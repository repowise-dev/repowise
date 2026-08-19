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

/** Map an absolute world rect to a screen rect. */
export function worldRectToScreen(cam: Camera, vp: Viewport, rect: Rect): Rect {
  const tl = worldToScreen(cam, vp, rect.x, rect.y);
  return {
    x: tl.sx,
    y: tl.sy,
    w: rect.w * cam.scale,
    h: rect.h * cam.scale,
  };
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

/**
 * A camera that fits the unit root box into the viewport with padding. The
 * shorter viewport axis bounds the scale so the whole square is visible.
 */
export function fitRoot(vp: Viewport, padding = 0.9): Camera {
  const scale = clampScale(Math.min(vp.w, vp.h) * padding);
  return { cx: 0.5, cy: 0.5, scale };
}

/**
 * A camera that frames an absolute world rect, centred, filling `fill` of the
 * smaller viewport axis. Used by "zoom to node" interactions.
 */
export function frameRect(vp: Viewport, rect: Rect, fill = 0.8): Camera {
  const span = Math.max(rect.w, rect.h, 1e-12);
  const scale = clampScale((Math.min(vp.w, vp.h) * fill) / span);
  return { cx: rect.x + rect.w / 2, cy: rect.y + rect.h / 2, scale };
}
