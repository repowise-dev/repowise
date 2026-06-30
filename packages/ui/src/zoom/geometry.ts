/**
 * Pure containment-tree geometry. Browser-free and unit-testable.
 *
 * The backend gives every node a `layout` rect in its parent's `[0,1]` space.
 * Composed down the tree this yields each node's **absolute world rect** inside
 * the root unit square. We precompute those once per scene; culling, the fade
 * `t`, hit-testing and "zoom to node" all read them, while the draw pass uses
 * the same rects to set each node's clip-and-scale transform.
 */

import type { Rect } from "./camera";
import { type GridLayoutOptions, gridLayout, type LayoutChild } from "./layout";
import type { ZoomNode } from "./types";

/** Compose a child's parent-space layout rect into absolute world space. */
export function composeRect(parentAbs: Rect, childLocal: Rect): Rect {
  return {
    x: parentAbs.x + childLocal.x * parentAbs.w,
    y: parentAbs.y + childLocal.y * parentAbs.h,
    w: childLocal.w * parentAbs.w,
    h: childLocal.h * parentAbs.h,
  };
}

const ROOT_RECT: Rect = { x: 0, y: 0, w: 1, h: 1 };

/**
 * Walk the tree from `rootId`, composing every node's absolute world rect.
 * Iterative (explicit stack) so a deep tree cannot overflow the call stack.
 * Each node's children are laid out client-side into a near-uniform grid (see
 * `layout.ts`) keyed off the parent's world aspect, then composed into world
 * space, so every card keeps a consistent shape and size encodes importance only
 * lightly. The backend treemap rects are intentionally ignored; unreachable
 * nodes are skipped.
 */
export function computeWorldRects(
  nodes: Map<string, ZoomNode>,
  rootId: string,
  opts: GridLayoutOptions = {},
): Map<string, Rect> {
  const rects = new Map<string, Rect>();
  const root = nodes.get(rootId);
  if (!root) return rects;
  rects.set(rootId, ROOT_RECT);

  const stack: string[] = [rootId];
  while (stack.length > 0) {
    const id = stack.pop()!;
    const node = nodes.get(id);
    const parentAbs = rects.get(id);
    if (!node || !parentAbs) continue;

    const kids: LayoutChild[] = [];
    for (const childId of node.children) {
      const child = nodes.get(childId);
      if (child) kids.push(child);
    }
    if (kids.length === 0) continue;

    const aspect = parentAbs.h > 0 ? parentAbs.w / parentAbs.h : 1;
    const local = gridLayout(kids, aspect, opts);
    for (const child of kids) {
      const lr = local.get(child.id);
      if (!lr) continue;
      rects.set(child.id, composeRect(parentAbs, lr));
      stack.push(child.id);
    }
  }
  return rects;
}

/**
 * The point on a rect's perimeter along the ray from its centre toward
 * `(towardX, towardY)`. Used to anchor relation arrows to box edges instead of
 * their centres (so a line visibly exits one box and enters another).
 */
export function perimeterPoint(
  rect: Rect,
  towardX: number,
  towardY: number,
): { x: number; y: number } {
  const cx = rect.x + rect.w / 2;
  const cy = rect.y + rect.h / 2;
  const dx = towardX - cx;
  const dy = towardY - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  const halfW = rect.w / 2;
  const halfH = rect.h / 2;
  // Largest t such that (t*dx, t*dy) stays within the half-extents.
  const tx = dx !== 0 ? halfW / Math.abs(dx) : Infinity;
  const ty = dy !== 0 ? halfH / Math.abs(dy) : Infinity;
  const t = Math.min(tx, ty);
  return { x: cx + dx * t, y: cy + dy * t };
}

/** Does a screen point fall inside a screen rect? (inclusive of edges) */
export function rectContains(rect: Rect, sx: number, sy: number): boolean {
  return sx >= rect.x && sx <= rect.x + rect.w && sy >= rect.y && sy <= rect.y + rect.h;
}
