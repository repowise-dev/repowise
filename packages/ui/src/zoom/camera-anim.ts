/**
 * Pure camera interpolation for fly-to-node, breadcrumb jumps and the tour.
 * Browser-free and unit-testable.
 *
 * A fly tweens the centre linearly in world space and the scale *geometrically*
 * (constant zoom-rate per unit time), which reads as a smooth, natural zoom
 * rather than a linear scale ramp that lurches at the end. Easing shapes the
 * progress so the motion accelerates out and settles in.
 */

import type { Camera } from "./camera";

export function clamp01(t: number): number {
  return t < 0 ? 0 : t > 1 ? 1 : t;
}

export function easeInOutCubic(t: number): number {
  const c = clamp01(t);
  return c < 0.5 ? 4 * c * c * c : 1 - (-2 * c + 2) ** 3 / 2;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/**
 * Camera at progress `t` in `[0,1]` along the fly from `from` to `to`. Centre
 * lerps linearly; scale interpolates geometrically so equal time steps cover
 * equal zoom ratios. `t` is eased internally, so callers pass raw linear
 * progress (elapsed / duration).
 */
export function interpolateCamera(from: Camera, to: Camera, t: number): Camera {
  const e = easeInOutCubic(t);
  const fromScale = from.scale > 0 ? from.scale : 1e-6;
  const ratio = to.scale > 0 ? to.scale / fromScale : 1;
  return {
    cx: lerp(from.cx, to.cx, e),
    cy: lerp(from.cy, to.cy, e),
    scale: fromScale * ratio ** e,
  };
}

/**
 * A fly duration that scales with how far the camera must travel: a small nudge
 * is quick, a cross-system jump takes longer, both clamped to a comfortable
 * range. Distance blends the centre move (in screen-ish units at the start
 * scale) with the zoom magnitude (log scale ratio) so a pure deep-zoom still
 * gets time to breathe.
 */
export function flyDuration(from: Camera, to: Camera, minMs = 260, maxMs = 720): number {
  const dCentre = Math.hypot(to.cx - from.cx, to.cy - from.cy) * from.scale;
  const fromScale = from.scale > 0 ? from.scale : 1e-6;
  const toScale = to.scale > 0 ? to.scale : 1e-6;
  const dZoom = Math.abs(Math.log(toScale / fromScale));
  const travel = Math.min(1, dCentre / 1200) + Math.min(1, dZoom / 6);
  return Math.round(minMs + (maxMs - minMs) * Math.min(1, travel / 2));
}
