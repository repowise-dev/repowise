/**
 * Keep the view across a re-layout. Pure, browser-free and unit-testable.
 *
 * The scene is rebuilt when the canvas aspect changes (the root box follows the
 * screen) or new data arrives, and every world rect may move. A camera left
 * where it was would show some other part of the map, so it follows the node
 * the user was looking into instead.
 */

import {
  type Camera,
  type Rect,
  type Viewport,
  UNIT_RECT,
  cameraAcrossRelayout,
  fitRoot,
} from "./camera";
import { focusChain } from "./focus-path";
import type { ZoomScene } from "./scene";

/** The root's world rect (see `rootRect`), or the unit square for an empty scene. */
export function sceneRootRect(scene: ZoomScene): Rect {
  return scene.worldRects.get(scene.rootId) ?? UNIT_RECT;
}

/** Two cameras equal to float noise. */
export function sameCamera(a: Camera, b: Camera): boolean {
  return (
    Math.abs(a.cx - b.cx) < 1e-9 &&
    Math.abs(a.cy - b.cy) < 1e-9 &&
    Math.abs(a.scale / b.scale - 1) < 1e-9
  );
}

/**
 * The camera to keep when `prev` is replaced by `next`. On the overview fit it
 * refits the new root; otherwise it follows the deepest focused node that is
 * still laid out, keeping it where it was on screen and at the same size.
 */
export function carryCamera(
  prev: ZoomScene,
  next: ZoomScene,
  cam: Camera,
  vp: Viewport,
): Camera {
  if (sameCamera(cam, fitRoot(vp, sceneRootRect(prev)))) return fitRoot(vp, sceneRootRect(next));
  const chain = focusChain(prev, cam, vp);
  for (let i = chain.length - 1; i >= 0; i--) {
    const id = chain[i]!.id;
    const from = prev.worldRects.get(id);
    const to = next.worldRects.get(id);
    if (from && to) return cameraAcrossRelayout(cam, from, to);
  }
  return cam;
}

/**
 * How far (relative) the canvas aspect must drift before the overview is laid
 * out again, so resize jitter and scrollbars never re-layout the map.
 */
export const RELAYOUT_ASPECT_TOLERANCE = 0.08;

/**
 * The root aspect to lay out for a `w` x `h` canvas: `current` until the
 * canvas shape has drifted past the tolerance, then the canvas aspect
 * (rounded, so near-equal sizes share a layout). Returns `current` itself when
 * nothing should change, so a React state setter bails out.
 */
export function settleRootAspect(current: number, w: number, h: number): number {
  if (w < 2 || h < 2) return current;
  const aspect = w / h;
  if (Math.abs(Math.log(aspect / current)) <= Math.log(1 + RELAYOUT_ASPECT_TOLERANCE)) {
    return current;
  }
  return Math.round(aspect * 100) / 100;
}
