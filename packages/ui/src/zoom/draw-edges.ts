/**
 * One level's relations among its children, drawn as routed directed
 * connectors (see `edges.ts`). Browser code.
 *
 * Two sets: the focused card's own relations at full strength, and, on the
 * level the camera looks into, its strongest few at rest (see `rest-links.ts`),
 * faint, dimmer still while a card of the level is focused. Both are routed
 * together so their fans share slots instead of overdrawing.
 *
 * A relation draws only when BOTH endpoints are drawn children big enough to
 * anchor an arrow (on screen, at least `EDGE_MIN_BOX_PX`), so an arrow never
 * points at a culled or density-capped sibling. The scene keeps each parent's
 * relations strongest first, so the strongest `EDGE_MAX_PER_PARENT` focused
 * (and `REST_EDGE_MAX` at-rest) relations are the first that qualify:
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
import { edgeStrokePx, isHiddenTestLink, REST_EDGE_MAX } from "./rest-links";
import type { ZoomScene } from "./scene";
import type { ZoomPalette } from "./theme";
import type { ZoomNode, ZoomRelation } from "./types";

const NO_EDGES: RoutedEdge[] = [];

/** Alpha of an at-rest link, and of one while a sibling card is focused. */
const REST_ALPHA = 0.45;
const REST_DIM_ALPHA = 0.15;

/** What `drawEdges` draws among one parent's children. */
export interface EdgeQuery {
  /** The hovered or selected card, whose relations draw at full strength. */
  focusId: string | null;
  /** Also draw this level's strongest relations at rest. */
  atRest: boolean;
  /** Draw only these verbs, or every verb when null. */
  relationVerbs: ReadonlySet<string> | null;
  /** Keep links touching the test suite in the at-rest set. */
  showTestLinks: boolean;
}

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

const touches = (r: ZoomRelation, id: string | null): boolean =>
  r.source_id === id || r.target_id === id;

/** Does relation `r` qualify for an arrow this frame? */
function wanted(f: EdgeFrame, r: ZoomRelation, relationVerbs: ReadonlySet<string> | null): boolean {
  if (r.source_id === r.target_id) return false;
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

function pushInput(r: ZoomRelation): void {
  inputs.push({
    id: `${r.source_id} ${r.target_id}`,
    sourceId: r.source_id,
    targetId: r.target_id,
    coupling: r.coupling,
    edgeCount: r.edge_count,
    label: r.label,
  });
}

/** Is `focusId` one of `parent`'s children (so the level's at-rest links dim)? */
export function focusedIn(scene: ZoomScene, parent: ZoomNode, focusId: string | null): boolean {
  return focusId !== null && scene.nodes.get(focusId)?.parent_id === parent.id;
}

/**
 * Draw the arrows among `parent`'s drawn `kids` that `q` asks for. Returns the
 * routed arrows to label: the focused card's when it is one of `kids`, the
 * at-rest set when nothing is focused, else none.
 */
export function drawEdges(
  ctx: CanvasRenderingContext2D,
  scene: ZoomScene,
  parent: ZoomNode,
  kids: readonly ZoomNode[],
  cam: Camera,
  vp: Viewport,
  palette: ZoomPalette,
  alpha: number,
  q: EdgeQuery,
): RoutedEdge[] {
  const rels = scene.relationsByParent.get(parent.id);
  if (!rels) return NO_EDGES;
  const f: EdgeFrame = { scene, parent, cam, vp, allDrawn: resetScratch(parent, kids) };
  const { focusId, relationVerbs } = q;
  if (focusId !== null) {
    for (const r of rels) {
      if (inputs.length >= EDGE_MAX_PER_PARENT) break;
      if (touches(r, focusId) && wanted(f, r, relationVerbs)) pushInput(r);
    }
  }
  const focused = inputs.length;
  if (q.atRest) {
    for (const r of rels) {
      if (inputs.length - focused >= REST_EDGE_MAX) break;
      // The focused card's links are the first set, drawn at full strength.
      if (touches(r, focusId)) continue;
      if (!q.showTestLinks && isHiddenTestLink(scene.nodes, r)) continue;
      if (wanted(f, r, relationVerbs)) pushInput(r);
    }
  }
  if (inputs.length === 0) return NO_EDGES;

  // `routeEdges` keeps input order (every endpoint is anchored), so the first
  // `focused` routes are the focused card's.
  const routed = routeEdges(inputs, anchored);
  const restAlpha = focusedIn(scene, parent, focusId) ? REST_DIM_ALPHA : REST_ALPHA;
  routed.forEach((e, i) => {
    const to = anchored.get(e.targetId)!;
    const withArrow = to.w >= ARROW_MIN_BOX_PX && to.h >= ARROW_MIN_BOX_PX;
    const a = alpha * (i < focused ? EDGE_FOCUS_ALPHA : restAlpha);
    drawEdge(ctx, e.route, e.coupling, palette, a, withArrow, edgeStrokePx(e.edgeCount));
  });
  if (focusedIn(scene, parent, focusId)) return focused === routed.length ? routed : routed.slice(0, focused);
  // A card focused elsewhere labels its own parent's arrows; one label set a
  // frame keeps the single-slot label memo (`edge-labels.ts`) from thrashing.
  return focusId === null ? routed : NO_EDGES;
}
