/**
 * The focused card's relations among its siblings, drawn as routed directed
 * connectors (see `edges.ts`). Browser code.
 *
 * Only relations touching the focused box are considered, and only those
 * whose BOTH endpoints are drawn children big enough to anchor an arrow (on
 * screen, at least `EDGE_MIN_BOX_PX`), so an arrow never points at a culled or
 * density-capped sibling. The scene keeps each parent's relations strongest
 * first, so the strongest `EDGE_MAX_PER_PARENT` are the first that qualify:
 * no per-frame filter or sort over the parent's whole bucket, and screen rects
 * are resolved only for the endpoints of candidate relations.
 *
 * The lookup tables are module scratch, cleared per call: a frame draws one
 * parent's arrows at a time and the routed edges keep no reference to them.
 */

import { type Camera, type Rect, type Viewport, worldRectToScreen } from "./camera";
import {
  ARROW_MIN_BOX_PX,
  EDGE_FOCUS_ALPHA,
  EDGE_MAX_PER_PARENT,
  EDGE_MIN_BOX_PX,
} from "./constants";
import { isOnScreen } from "./cull";
import { type EdgeInput, type RoutedEdge, routeEdges } from "./edges";
import { drawEdge } from "./nodes";
import type { ZoomScene } from "./scene";
import type { ZoomPalette } from "./theme";
import type { ZoomNode, ZoomRelation } from "./types";

const NO_EDGES: RoutedEdge[] = [];

/** Screen rects of the endpoints that anchor an arrow this call. */
const anchored = new Map<string, Rect>();
/** Endpoints already found unable to anchor one. */
const rejected = new Set<string>();
/** The drawn children, when the scene dropped some (see `isDrawnChild`). */
const drawnKids = new Set<string>();
/** Screen rects handed out to `anchored`, reused call to call. */
const rectPool: Rect[] = [];
const inputs: EdgeInput[] = [];

interface EdgeFrame {
  scene: ZoomScene;
  parent: ZoomNode;
  cam: Camera;
  vp: Viewport;
  /** Every child was drawn, so "drawn" is just "a child of `parent`". */
  allDrawn: boolean;
}

/**
 * An endpoint must be one of the children drawn this frame. When none were
 * dropped (the common case) that is just "a child of this parent"; a
 * density-capped or partly pruned parent needs the drawn set itself.
 */
function isDrawnChild(f: EdgeFrame, id: string): boolean {
  return f.allDrawn ? f.scene.nodes.get(id)?.parent_id === f.parent.id : drawnKids.has(id);
}

function anchors(f: EdgeFrame, id: string): boolean {
  if (anchored.has(id)) return true;
  if (rejected.has(id)) return false;
  const wr = f.scene.worldRects.get(id);
  if (wr && isDrawnChild(f, id)) {
    const i = anchored.size;
    const s = worldRectToScreen(f.cam, f.vp, wr, rectPool[i] ?? (rectPool[i] = { x: 0, y: 0, w: 0, h: 0 }));
    if (isOnScreen(s, f.vp) && s.w >= EDGE_MIN_BOX_PX) {
      anchored.set(id, s);
      return true;
    }
  }
  rejected.add(id);
  return false;
}

/** Does relation `r` qualify for an arrow this frame? */
function wanted(
  f: EdgeFrame,
  r: ZoomRelation,
  focusId: string,
  relationVerbs: ReadonlySet<string> | null,
): boolean {
  if (r.source_id === r.target_id) return false;
  if (r.source_id !== focusId && r.target_id !== focusId) return false;
  if (relationVerbs !== null && !relationVerbs.has(r.label)) return false;
  return anchors(f, r.source_id) && anchors(f, r.target_id);
}

function resetScratch(parent: ZoomNode, kids: readonly ZoomNode[]): boolean {
  anchored.clear();
  rejected.clear();
  drawnKids.clear();
  inputs.length = 0;
  const allDrawn = kids.length >= parent.children.length;
  if (!allDrawn) for (const k of kids) drawnKids.add(k.id);
  return allDrawn;
}

/** Draw the arrows among `parent`'s drawn `kids` that touch `focusId`; returns them routed. */
export function drawEdges(
  ctx: CanvasRenderingContext2D,
  scene: ZoomScene,
  parent: ZoomNode,
  kids: readonly ZoomNode[],
  cam: Camera,
  vp: Viewport,
  palette: ZoomPalette,
  alpha: number,
  focusId: string,
  relationVerbs: ReadonlySet<string> | null,
): RoutedEdge[] {
  const rels = scene.relationsByParent.get(parent.id);
  if (!rels) return NO_EDGES;
  const f: EdgeFrame = { scene, parent, cam, vp, allDrawn: resetScratch(parent, kids) };
  for (const r of rels) {
    if (inputs.length >= EDGE_MAX_PER_PARENT) break;
    if (!wanted(f, r, focusId, relationVerbs)) continue;
    inputs.push({
      id: `${r.source_id} ${r.target_id}`,
      sourceId: r.source_id,
      targetId: r.target_id,
      coupling: r.coupling,
      edgeCount: r.edge_count,
      label: r.label,
    });
  }
  if (inputs.length === 0) return NO_EDGES;

  const routed = routeEdges(inputs, anchored);
  for (const e of routed) {
    const to = anchored.get(e.targetId)!;
    const withArrow = to.w >= ARROW_MIN_BOX_PX && to.h >= ARROW_MIN_BOX_PX;
    drawEdge(ctx, e.route, e.coupling, palette, alpha * EDGE_FOCUS_ALPHA, withArrow);
  }
  return routed;
}
