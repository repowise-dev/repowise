/**
 * Pure containment-tree geometry. Browser-free and unit-testable.
 *
 * `layout.ts` packs each parent's children into rects in that parent's `[0,1]`
 * space. Composed down the tree this yields each node's **absolute world rect** inside
 * the root unit square. We precompute those once per scene; culling, the fade
 * `t`, hit-testing and "zoom to node" all read them, while the draw pass uses
 * the same rects to set each node's clip-and-scale transform.
 */

import type { Rect } from "./camera";
import { type LayoutChild, type LayoutRelation, packLayout, type PackLayoutOptions } from "./layout";
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

/** Layout options for the whole tree: the per-parent pack plus the root's shape. */
export interface WorldLayoutOptions extends PackLayoutOptions {
  /**
   * Width : height of the root box, normally the canvas aspect, so the top
   * level packs to the screen and fills it. Defaults to a square.
   */
  rootAspect?: number;
}

/**
 * Each parent's last pack, in its own `[0,1]` space, and the box aspect it was
 * packed for. A pack depends only on the children and that aspect, so a
 * re-layout that changes the root's shape repacks the root and reuses every
 * parent whose box kept its shape (all of them below the root: each card keeps
 * its aspect at any depth, see `packLayout`).
 */
export type PackCache = Map<string, { aspect: number; local: Map<string, Rect> }>;

/** Two box aspects equal to float noise (composition rounds in the last bits). */
export function sameAspect(a: number, b: number): boolean {
  return Math.abs(a / b - 1) < 1e-9;
}

/** Root aspects past this range would leave the cards a sliver of the screen. */
const ROOT_ASPECT_MIN = 0.5;
const ROOT_ASPECT_MAX = 3;

/**
 * The root's world rect: the largest box of `aspect` centred in the unit
 * square. Staying inside `[0,1]` keeps the camera clamp, the precision rebase
 * and the minimap mapping unchanged; only the overview's shape follows the
 * screen.
 */
export function rootRect(aspect = 1): Rect {
  const a = Math.min(ROOT_ASPECT_MAX, Math.max(ROOT_ASPECT_MIN, aspect || 1));
  return a >= 1
    ? { x: 0, y: (1 - 1 / a) / 2, w: 1, h: 1 / a }
    : { x: (1 - a) / 2, y: 0, w: a, h: 1 };
}

/**
 * Walk the tree from `rootId` (placed by `rootRect`), composing every node's
 * absolute world rect.
 * Iterative (explicit stack) so a deep tree cannot overflow the call stack.
 * Each node's children are laid out client-side as an importance-weighted
 * pack (see `layout.ts`) keyed off the parent's world aspect and ranked by
 * their sibling links in `relations` (keyed by parent id), then composed into
 * world space, so card size encodes importance and the rows read in
 * dependency order; children in `aside` get a side column (see `packLayout`).
 * Nodes unreachable from the root are skipped. `contentBox`, when
 * given, narrows the box a node's children pack into (a rail kept for its
 * external systems, see `externals.ts`). `packs`, when given, is read and
 * refreshed so a parent whose box aspect is unchanged is not packed again; it
 * is only valid for the same `nodes`, `relations`, `aside` and pack options.
 */
export function computeWorldRects(
  nodes: Map<string, ZoomNode>,
  rootId: string,
  opts: WorldLayoutOptions = {},
  contentBox?: (id: string, rect: Rect) => Rect,
  packs?: PackCache,
  relations?: ReadonlyMap<string, readonly LayoutRelation[]>,
  aside?: ReadonlySet<string>,
): Map<string, Rect> {
  const rects = new Map<string, Rect>();
  const root = nodes.get(rootId);
  if (!root) return rects;
  rects.set(rootId, rootRect(opts.rootAspect));

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

    const box = contentBox ? contentBox(id, parentAbs) : parentAbs;
    const aspect = box.h > 0 ? box.w / box.h : 1;
    const local = packFor(id, kids, aspect, opts, packs, relations?.get(id), aside);
    for (const child of kids) {
      const lr = local.get(child.id);
      if (!lr) continue;
      rects.set(child.id, composeRect(box, lr));
      stack.push(child.id);
    }
  }
  return rects;
}

function packFor(
  id: string,
  kids: readonly LayoutChild[],
  aspect: number,
  opts: PackLayoutOptions,
  packs: PackCache | undefined,
  relations: readonly LayoutRelation[] | undefined,
  aside: ReadonlySet<string> | undefined,
): Map<string, Rect> {
  const hit = packs?.get(id);
  if (hit && sameAspect(hit.aspect, aspect)) return hit.local;
  const local = packLayout(kids, aspect, opts, relations, aside);
  packs?.set(id, { aspect, local });
  return local;
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
