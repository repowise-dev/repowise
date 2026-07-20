/**
 * Pure continuous-zoom fade math. Browser-free and unit-testable.
 *
 * A single per-node scalar `t` drives the cross-fade. `t` is derived from the
 * node's on-screen pixel width: while a node is small it is drawn as one solid
 * card (`t = 0`); as you zoom in and it grows past `start` px, its body fades
 * out while its children fade in, completing at `end` px (`t = 1`). The same
 * `t` makes a card *visibly open into its children* rather than being remounted.
 */

import {
  END_FRACTION,
  END_MAX_PX,
  END_MIN_PX,
  START_FRACTION,
  START_MAX_PX,
  START_MIN_PX,
} from "./constants";

export interface FadeThresholds {
  /** Screen-px node width where the body begins to fade and children appear. */
  start: number;
  /** Screen-px node width where the body is gone and children are fully in. */
  end: number;
}

export interface FadeAlphas {
  /** Alpha for the node's own card body (label, fill, chips). */
  body: number;
  /** Base alpha handed to the node's children subtree. */
  child: number;
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/**
 * Fade thresholds scale with canvas width so the experience is consistent
 * across a phone and a 4K monitor. Clamped to sane absolute bounds.
 */
export function expandThresholds(canvasW: number): FadeThresholds {
  const start = Math.min(START_MAX_PX, Math.max(START_MIN_PX, canvasW * START_FRACTION));
  const end = Math.min(END_MAX_PX, Math.max(END_MIN_PX, canvasW * END_FRACTION));
  // Guarantee a non-degenerate interval even after clamping.
  return { start, end: Math.max(end, start + 1) };
}

/**
 * The transition scalar for a node `screenW` pixels wide. `hasChildren` is
 * false for file leaves, which never cross-fade (they are the deepest content).
 */
export function transitionT(
  screenW: number,
  thresholds: FadeThresholds,
  hasChildren: boolean,
): number {
  if (!hasChildren) return 0;
  return clamp01((screenW - thresholds.start) / (thresholds.end - thresholds.start));
}

/**
 * Split an inherited alpha into the body alpha (fades out with `t`) and the
 * child alpha (fades in with `t`).
 */
export function fadeAlphas(inherited: number, t: number): FadeAlphas {
  return { body: inherited * (1 - t), child: inherited * t };
}

/**
 * Leaf-cap: once a leaf would grow past `end` px it stops growing on screen, so
 * a file card "freezes" at a readable size while the camera keeps zooming into
 * empty space. Returns a scale `<= 1` to apply about the node's centre, or `1`
 * when no cap is needed.
 */
export function leafCapScale(
  screenW: number,
  thresholds: FadeThresholds,
  hasChildren: boolean,
): number {
  if (hasChildren || screenW <= thresholds.end) return 1;
  return thresholds.end / screenW;
}
