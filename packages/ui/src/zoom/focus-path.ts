/**
 * "You are here" focus tracking. Pure, browser-free and unit-testable.
 *
 * The breadcrumb and the URL need to know which node the camera is currently
 * looking *into*. We walk from the root following whichever child contains the
 * viewport centre, descending only while that child has grown to fill a good
 * fraction of the viewport (i.e. you have actually zoomed into it). The chain
 * returned is root -> ... -> focus; its last element is the current focus node.
 */

import type { Camera, Viewport } from "./camera";
import type { ZoomScene } from "./scene";
import type { ZoomNode } from "./types";

/** A child must fill at least this fraction of the viewport width to be "entered". */
const ENTER_FRACTION = 0.62;

export function focusChain(
  scene: ZoomScene,
  cam: Camera,
  vp: Viewport,
  enterFraction = ENTER_FRACTION,
): ZoomNode[] {
  const chain: ZoomNode[] = [];
  let current = scene.nodes.get(scene.rootId);
  // The viewport centre in world space is exactly the camera centre.
  const cxWorld = cam.cx;
  const cyWorld = cam.cy;

  while (current) {
    chain.push(current);
    let next: ZoomNode | null = null;
    for (const childId of current.children) {
      const r = scene.worldRects.get(childId);
      if (!r) continue;
      if (cxWorld >= r.x && cxWorld <= r.x + r.w && cyWorld >= r.y && cyWorld <= r.y + r.h) {
        // Only descend once the child fills enough of the screen to count as
        // entered. `r.w * cam.scale` is the child's width in screen pixels
        // (cam.scale is screen px per world unit, matching worldRectToScreen).
        if (r.w * cam.scale >= vp.w * enterFraction) next = scene.nodes.get(childId) ?? null;
        break;
      }
    }
    if (!next) break;
    current = next;
  }
  return chain;
}

/** The current focus node id (deepest entered), or the root id. */
export function focusId(scene: ZoomScene, cam: Camera, vp: Viewport): string {
  const chain = focusChain(scene, cam, vp);
  return chain.length > 0 ? chain[chain.length - 1]!.id : scene.rootId;
}
