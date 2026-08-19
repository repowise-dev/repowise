/**
 * Pure viewport culling and level-of-detail. Browser-free.
 *
 * These keep the live drawn-node count bounded no matter how large the tree is:
 *  - `isOnScreen`  drops subtrees whose screen rect lies outside the viewport.
 *  - `MIN_DRAW_PX` (in the renderer) drops nodes too small to see.
 *  - `selectChildren` is only a safety ceiling for a pathologically flat folder;
 *    normal parents draw all their children and rely on the two gates above.
 */

import type { Rect, Viewport } from "./camera";
import { CULL_MARGIN_PX } from "./constants";
import type { ZoomNode } from "./types";

/** Is any part of a screen rect within the viewport (plus a pan margin)? */
export function isOnScreen(screenRect: Rect, vp: Viewport, margin = CULL_MARGIN_PX): boolean {
  return (
    screenRect.x + screenRect.w > -margin &&
    screenRect.y + screenRect.h > -margin &&
    screenRect.x < vp.w + margin &&
    screenRect.y < vp.h + margin
  );
}

/**
 * Choose which children to draw: the top `cap` by `sibling_rank` (1 = most
 * important). Pure and deterministic; ties break by id so the selection is
 * stable frame-to-frame. Returns the ids in their original child order so the
 * treemap layout (which the renderer reads from each node) still lines up.
 */
export function selectChildren(
  children: readonly ZoomNode[],
  cap: number,
): ZoomNode[] {
  if (children.length <= cap) return [...children];
  const ranked = [...children].sort(
    (a, b) => a.sibling_rank - b.sibling_rank || (a.id < b.id ? -1 : 1),
  );
  const keep = new Set(ranked.slice(0, cap).map((c) => c.id));
  return children.filter((c) => keep.has(c.id));
}
