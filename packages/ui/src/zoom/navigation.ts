/**
 * Keyboard navigation over the laid-out tree. Pure, browser-free and
 * unit-testable: the canvas maps keys onto these and moves the camera.
 */

import { type Rect, type Viewport, frameRect } from "./camera";
import { FRAME_FILL, focusId } from "./focus-path";
import type { ZoomScene } from "./scene";
import type { ZoomNode } from "./types";

export type Direction = "left" | "right" | "up" | "down";

/**
 * The sibling of `id` nearest in `dir`, by card centres, or null at the edge.
 * Distance along the arrow counts once and drift across it twice, so "right"
 * picks the card beside you over one further along a lower row.
 */
export function siblingInDirection(scene: ZoomScene, id: string, dir: Direction): string | null {
  const node = scene.nodes.get(id);
  const from = scene.worldRects.get(id);
  const parent = node?.parent_id ? scene.nodes.get(node.parent_id) : undefined;
  if (!from || !parent) return null;
  let best: string | null = null;
  let bestScore = Infinity;
  for (const sibId of parent.children) {
    const r = sibId === id ? undefined : scene.worldRects.get(sibId);
    const score = r ? directionalScore(from, r, dir) : Infinity;
    if (score < bestScore) {
      bestScore = score;
      best = sibId;
    }
  }
  return best;
}

/** How far `to` is from `from` heading `dir`, or Infinity when it is not ahead. */
function directionalScore(from: Rect, to: Rect, dir: Direction): number {
  const dx = to.x + to.w / 2 - (from.x + from.w / 2);
  const dy = to.y + to.h / 2 - (from.y + from.h / 2);
  const horizontal = dir === "left" || dir === "right";
  const sign = dir === "right" || dir === "down" ? 1 : -1;
  const along = (horizontal ? dx : dy) * sign;
  return along <= 1e-9 ? Infinity : along + 2 * Math.abs(horizontal ? dy : dx);
}

/**
 * Where "up one level" goes from a focus chain: the nearest ancestor whose
 * frame actually leaves the current focus, or null for the whole-system fit.
 * Framing the parent of a card that fills it would keep that card in focus, so
 * "up" would look stuck; such an ancestor is skipped.
 */
export function upTarget(scene: ZoomScene, chain: readonly ZoomNode[], vp: Viewport): string | null {
  const focus = chain[chain.length - 1]?.id;
  for (let i = chain.length - 2; i >= 1; i--) {
    const rect = scene.worldRects.get(chain[i]!.id);
    if (rect && focusId(scene, frameRect(vp, rect, FRAME_FILL), vp) !== focus) return chain[i]!.id;
  }
  return null;
}
