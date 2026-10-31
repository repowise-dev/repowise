/**
 * "You are here" focus tracking. Pure, browser-free and unit-testable.
 *
 * The breadcrumb and the URL need to know which node the camera is currently
 * looking *into*. We walk from the root following whichever child contains the
 * viewport centre, descending only while that child has grown to fill a good
 * fraction of the viewport (i.e. you have actually zoomed into it). The chain
 * returned is root -> ... -> focus; its last element is the current focus node.
 *
 * "Fills the viewport" is `viewportCoverage`, the same measure `frameRect` sizes
 * a dive by, and a dive frames at `FRAME_FILL` > `ENTER_FRACTION`. Measuring
 * the child against the viewport width alone left every dive on a landscape
 * canvas short of "entered", so the breadcrumb never moved. Both measure the
 * clear viewport (`Viewport.insetRight`), so a dive beside an open sheet still
 * counts as entered.
 */

import { type Camera, type Viewport, clearCentre, viewportCoverage } from "./camera";
import { type ExternalLane, focusLanes } from "./externals";
import type { ZoomScene } from "./scene";
import type { ZoomNode } from "./types";

/** A child must cover at least this fraction of the viewport to be "entered". */
export const ENTER_FRACTION = 0.62;
/** How much of the viewport a dive (`frameNode`) frames a node at. */
export const FRAME_FILL = 0.8;

export function focusChain(
  scene: ZoomScene,
  cam: Camera,
  vp: Viewport,
  enterFraction = ENTER_FRACTION,
): ZoomNode[] {
  const chain: ZoomNode[] = [];
  let current = scene.nodes.get(scene.rootId);
  // The centre of the part of the viewport no sheet covers, where a dive lands.
  const { wx: cxWorld, wy: cyWorld } = clearCentre(cam, vp);

  while (current) {
    chain.push(current);
    let next: ZoomNode | null = null;
    for (const childId of current.children) {
      const r = scene.worldRects.get(childId);
      if (!r) continue;
      if (cxWorld >= r.x && cxWorld <= r.x + r.w && cyWorld >= r.y && cyWorld <= r.y + r.h) {
        // Only descend once the child fills enough of the screen to count as entered.
        if (viewportCoverage(cam, vp, r) >= enterFraction) next = scene.nodes.get(childId) ?? null;
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

/** `FocusTracker.update` result bits. */
export const FOCUS_MOVED = 1;
export const RAILS_MOVED = 2;

/**
 * The focus as of the last drawn frame: its chain and the external rails
 * trimmed for it (see `focusLanes`), re-derived only when the focus moves.
 */
export class FocusTracker {
  private id: string | null = null;
  chain: ZoomNode[] = [];
  lanes: ReadonlyMap<string, ExternalLane> | null = null;

  /**
   * A new scene: its rails are re-derived on the next update. New data (new
   * nodes) reports the focus again; a re-layout of the same map keeps it.
   */
  reset(newData: boolean): void {
    this.lanes = null;
    if (newData) this.id = null;
  }

  /**
   * Re-read the focus for a drawn frame. Returns `FOCUS_MOVED` when the focused
   * node changed (report the chain) and `RAILS_MOVED` when the frame just
   * drawn used other rails than the new ones (repaint once).
   */
  update(scene: ZoomScene, cam: Camera, vp: Viewport): number {
    const chain = focusChain(scene, cam, vp);
    const id = chain.length > 0 ? chain[chain.length - 1]!.id : scene.rootId;
    if (id === this.id && this.lanes !== null) return 0;
    this.chain = chain;
    const lanes = focusLanes(scene.externalLanes, chain);
    const rails = lanes.size > 0 || (this.lanes?.size ?? 0) > 0 ? RAILS_MOVED : 0;
    this.lanes = lanes;
    if (id === this.id) return rails;
    this.id = id;
    return rails | FOCUS_MOVED;
  }
}
