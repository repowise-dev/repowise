/**
 * Recursive clip-and-scale draw of the containment tree. Browser code.
 *
 * Each node resolves to a screen rect from its absolute world rect and the
 * camera. We draw the node's card (fading out as `t` grows), then clip to its
 * rect and recurse into the top-N children (fading in), so a card *visibly
 * opens into its parts* as you zoom. Off-screen subtrees, sub-pixel nodes and
 * fully-faded subtrees are skipped, and a density cap bounds how many children
 * are drawn at any depth, keeping the live node count bounded on huge trees.
 */

import { type Camera, type Rect, type Viewport, worldRectToScreen } from "./camera";
import {
  ALPHA_EPSILON,
  ARROW_MIN_BOX_PX,
  DOT_GRID_RADIUS_PX,
  DOT_GRID_SPACING_PX,
  EDGE_FOCUS_ALPHA,
  EDGE_MAX_PER_PARENT,
  EDGE_MIN_BOX_PX,
  MAX_CHILDREN_DRAWN,
  MIN_DRAW_PX,
} from "./constants";
import { isOnScreen, selectChildren } from "./cull";
import { type EdgeInput, routeEdges } from "./edges";
import { drawCard, drawEdge } from "./nodes";
import { childNodes, type ZoomScene } from "./scene";
import type { ZoomPalette } from "./theme";
import type { ZoomNode } from "./types";
import {
  expandThresholds,
  fadeAlphas,
  leafCapScale,
  transitionT,
} from "./zoom-transition";

export interface DrawOptions {
  selectedId: string | null;
  hoveredId: string | null;
  lowDetail: boolean;
}

/**
 * A faint dot-grid under the cards, anchored to the camera so it parallaxes on
 * pan and reads as a designed surface (not a blank fill). Cheap: 1px rects, and
 * the subtlety comes from the low-alpha `--color-canvas-dot` token.
 */
function drawDotGrid(
  ctx: CanvasRenderingContext2D,
  cam: Camera,
  vp: Viewport,
  palette: ZoomPalette,
): void {
  const sp = DOT_GRID_SPACING_PX;
  const ox = (((vp.w / 2 - cam.cx * cam.scale) % sp) + sp) % sp;
  const oy = (((vp.h / 2 - cam.cy * cam.scale) % sp) + sp) % sp;
  ctx.fillStyle = palette.dot;
  const r = DOT_GRID_RADIUS_PX;
  for (let x = ox; x < vp.w; x += sp) {
    for (let y = oy; y < vp.h; y += sp) ctx.fillRect(x, y, r, r);
  }
}

export interface PickEntry {
  id: string;
  rect: Rect;
  depth: number;
}

export interface DrawStats {
  drawn: number;
  culled: number;
  maxDepthDrawn: number;
  /** Drawn cards, deepest last, for hit-testing the most recent frame. */
  pick: PickEntry[];
}

/** Shrink a rect about its centre by `scale` (used for the leaf cap). */
function shrinkAboutCentre(rect: Rect, scale: number): Rect {
  if (scale >= 1) return rect;
  const w = rect.w * scale;
  const h = rect.h * scale;
  return { x: rect.x + (rect.w - w) / 2, y: rect.y + (rect.h - h) / 2, w, h };
}

export function drawScene(
  ctx: CanvasRenderingContext2D,
  scene: ZoomScene,
  cam: Camera,
  vp: Viewport,
  palette: ZoomPalette,
  opts: DrawOptions,
): DrawStats {
  const thresholds = expandThresholds(vp.w);
  const stats: DrawStats = { drawn: 0, culled: 0, maxDepthDrawn: 0, pick: [] };

  ctx.fillStyle = palette.bg;
  ctx.fillRect(0, 0, vp.w, vp.h);
  if (!opts.lowDetail) drawDotGrid(ctx, cam, vp, palette);

  const root = scene.nodes.get(scene.rootId);
  if (!root) return stats;

  // The node under the cursor (or selected) lifts its incident relations out of
  // the quiet baseline. Hover wins for immediate feedback.
  const focusId = opts.hoveredId ?? opts.selectedId;

  const drawNode = (node: ZoomNode, inheritedAlpha: number, depth: number): void => {
    const worldRect = scene.worldRects.get(node.id);
    if (!worldRect) return;
    const screen = worldRectToScreen(cam, vp, worldRect);

    if (!isOnScreen(screen, vp)) {
      stats.culled++;
      return;
    }
    if (screen.w < MIN_DRAW_PX || screen.h < MIN_DRAW_PX) {
      stats.culled++;
      return;
    }

    const kids = childNodes(scene, node);
    const hasChildren = kids.length > 0;
    const t = transitionT(screen.w, thresholds, hasChildren);
    const { body, child } = fadeAlphas(inheritedAlpha, t);

    // Leaf cap: a file stops growing once it fills enough of the screen.
    const cap = leafCapScale(screen.w, thresholds, hasChildren);
    const drawnRect = cap < 1 ? shrinkAboutCentre(screen, cap) : screen;

    if (body > ALPHA_EPSILON) {
      drawCard(ctx, drawnRect, node, palette, body, {
        selected: node.id === opts.selectedId,
        hovered: node.id === opts.hoveredId,
        lowDetail: opts.lowDetail,
      }, t);
    }
    stats.drawn++;
    stats.maxDepthDrawn = Math.max(stats.maxDepthDrawn, depth);
    stats.pick.push({ id: node.id, rect: drawnRect, depth });

    if (!hasChildren || child <= ALPHA_EPSILON) return;

    ctx.save();
    ctx.beginPath();
    ctx.rect(screen.x, screen.y, screen.w, screen.h);
    ctx.clip();

    // Draw every child (uniform cells fade and grow in together); the cap is a
    // safety net for a pathologically flat folder, dropping the least-important
    // tail so the frame stays cheap. Each child still self-culls below.
    const visible =
      kids.length > MAX_CHILDREN_DRAWN ? selectChildren(kids, MAX_CHILDREN_DRAWN) : kids;

    // Screen rects of the children big enough to anchor an arrow to. Edges are
    // drawn (behind the cards) only between boxes that are actually on screen,
    // so an arrow can never point at a culled or density-capped sibling.
    const childRects = new Map<string, Rect>();
    for (const kid of visible) {
      const wr = scene.worldRects.get(kid.id);
      if (!wr) continue;
      const r = worldRectToScreen(cam, vp, wr);
      if (isOnScreen(r, vp) && r.w >= EDGE_MIN_BOX_PX) childRects.set(kid.id, r);
    }
    drawEdges(ctx, scene, node, childRects, palette, child, opts.lowDetail, focusId);

    for (const kid of visible) drawNode(kid, child, depth + 1);

    ctx.restore();
  };

  drawNode(root, 1, 0);
  return stats;
}

/**
 * Draw the relations among the children of a parent we are zoomed into, as
 * routed directed connectors (see `edges.ts`). Only edges whose BOTH endpoints
 * are in `childRects` (currently drawn) are considered; the rest are dropped.
 * The set is capped to the strongest `EDGE_MAX_PER_PARENT` by edge count so a
 * dense level never sprays the canvas, then routed so edges that share a box
 * side fan out and curve around the boxes between their endpoints.
 */
function drawEdges(
  ctx: CanvasRenderingContext2D,
  scene: ZoomScene,
  parent: ZoomNode,
  childRects: Map<string, Rect>,
  palette: ZoomPalette,
  alpha: number,
  lowDetail: boolean,
  focusId: string | null,
): void {
  // Relations are revealed only for the box the user is pointing at / has
  // selected, so the canvas is not a thicket of arrows. No focus -> no edges.
  if (lowDetail || childRects.size < 2 || focusId === null) return;
  const rels = scene.relationsByParent.get(parent.id);
  if (!rels) return;

  const drawable = rels.filter(
    (r) =>
      r.source_id !== r.target_id &&
      (r.source_id === focusId || r.target_id === focusId) &&
      childRects.has(r.source_id) &&
      childRects.has(r.target_id),
  );
  if (drawable.length === 0) return;
  drawable.sort((a, b) => b.edge_count - a.edge_count);
  const top = drawable.length > EDGE_MAX_PER_PARENT ? drawable.slice(0, EDGE_MAX_PER_PARENT) : drawable;

  const inputs: EdgeInput[] = top.map((r) => ({
    id: `${r.source_id} ${r.target_id}`,
    sourceId: r.source_id,
    targetId: r.target_id,
    coupling: r.coupling,
    edgeCount: r.edge_count,
  }));
  for (const routed of routeEdges(inputs, childRects)) {
    const to = childRects.get(routed.targetId)!;
    const withArrow = to.w >= ARROW_MIN_BOX_PX && to.h >= ARROW_MIN_BOX_PX;
    drawEdge(ctx, routed.route, routed.coupling, palette, alpha * EDGE_FOCUS_ALPHA, withArrow);
  }
}

/** Deepest drawn card containing the screen point, or null. Pure. */
export function pickNode(stats: DrawStats, sx: number, sy: number): string | null {
  let best: PickEntry | null = null;
  for (const entry of stats.pick) {
    const r = entry.rect;
    if (sx < r.x || sx > r.x + r.w || sy < r.y || sy > r.y + r.h) continue;
    if (!best || entry.depth >= best.depth) best = entry;
  }
  return best ? best.id : null;
}
