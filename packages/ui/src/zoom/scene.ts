/**
 * Pure scene model derived from the fetched zoom map. Browser-free.
 *
 * Indexes the flat node list by id, precomputes every node's absolute world
 * rect (see `geometry.ts`) and buckets relations by their `parent_id` so the
 * renderer can pull just the relations relevant to whichever node it has zoomed
 * into. Rebuilt only when a new map arrives (or a deeper subtree is fetched),
 * never per frame.
 *
 * Everything the draw pass would otherwise rebuild per frame is resolved here
 * once: each node's drawable children (laid out, density-capped) and each
 * parent's relations pre-sorted strongest first, so a frame allocates no child
 * arrays and sorts nothing.
 *
 * A canvas resize re-lays the same map out to a new root shape. Only the
 * geometry follows the root aspect, so everything else is kept per map and
 * reused, and the per-parent packs are cached (see `PackCache`): a re-layout
 * repacks the root and recomposes rects, nothing more.
 */

import { containerBadge } from "./c4";
import type { Rect } from "./camera";
import { setCardBadge } from "./card-content";
import { MAX_CHILDREN_DRAWN } from "./constants";
import { selectChildren } from "./cull";
import {
  bucketExternalRelations,
  buildExternalLanes,
  type ExternalLane,
  type LaneCache,
  railContent,
} from "./externals";
import { type PackCache, computeWorldRects, type WorldLayoutOptions } from "./geometry";
import type { ZoomContainer, ZoomExternal, ZoomMap, ZoomNode, ZoomRelation } from "./types";

export interface ZoomScene {
  rootId: string;
  nodes: Map<string, ZoomNode>;
  worldRects: Map<string, Rect>;
  /** Relations per parent, strongest (`edge_count`) first. */
  relationsByParent: Map<string, ZoomRelation[]>;
  /**
   * Each parent's children the draw pass visits: laid out, in layout order, and
   * capped to `MAX_CHILDREN_DRAWN` by `sibling_rank`. Leaves have no entry.
   */
  drawChildren: Map<string, readonly ZoomNode[]>;
  /**
   * Each parent's external systems: the rail its children leave room for, the
   * dashed cards in it and the routed connectors (see `externals.ts`).
   */
  externalLanes: Map<string, ExternalLane>;
  /** Total nodes that received a world rect (reachable, laid-out). */
  laidOutCount: number;
}

/** What a scene derives from its map regardless of the root shape. */
interface MapDerived {
  nodes: Map<string, ZoomNode>;
  externals: Map<string, ZoomExternal>;
  extBuckets: ReturnType<typeof bucketExternalRelations>;
  /** Unset until the first layout: they need to know which nodes are laid out. */
  relationsByParent?: Map<string, ZoomRelation[]>;
  drawChildren?: Map<string, readonly ZoomNode[]>;
  packs: PackCache;
  lanes: LaneCache;
}

/** Per map, for re-layouts that change only `rootAspect`. */
const derivedByMap = new WeakMap<ZoomMap, MapDerived>();

function deriveMap(map: ZoomMap): MapDerived {
  const nodes = new Map<string, ZoomNode>();
  for (const n of map.nodes) nodes.set(n.id, n);
  const externals = new Map<string, ZoomExternal>();
  for (const e of map.externals ?? []) externals.set(e.id, e);
  const extBuckets = bucketExternalRelations(map.external_relations ?? [], nodes, externals);
  return { nodes, externals, extBuckets, packs: new Map(), lanes: new Map() };
}

/**
 * Only a root shape is given, so the map's derived data and packs can be
 * shared with its other layouts; custom pack options get a layout of their own.
 */
function onlyRootAspect(layout: WorldLayoutOptions): boolean {
  for (const k in layout) if (k !== "rootAspect") return false;
  return true;
}

export function buildScene(map: ZoomMap, layout: WorldLayoutOptions = {}): ZoomScene {
  const shared = onlyRootAspect(layout);
  let d = shared ? derivedByMap.get(map) : undefined;
  if (!d) {
    d = deriveMap(map);
    if (shared) derivedByMap.set(map, d);
  }
  const { nodes, externals, extBuckets } = d;
  const worldRects = computeWorldRects(
    nodes,
    map.root_id,
    layout,
    (id, r) => (extBuckets.has(id) ? railContent(r) : r),
    shared ? d.packs : undefined,
  );
  // Which nodes are laid out does not depend on the root shape, so these hold
  // for every layout of the map.
  if (!d.relationsByParent || !d.drawChildren) {
    d.relationsByParent = bucketRelations(map.relations, worldRects);
    d.drawChildren = resolveDrawChildren(nodes, worldRects);
    setContainerBadges(nodes, map.containers ?? []);
  }

  return {
    rootId: map.root_id,
    nodes,
    worldRects,
    relationsByParent: d.relationsByParent,
    drawChildren: d.drawChildren,
    externalLanes: buildExternalLanes(extBuckets, externals, worldRects, shared ? d.lanes : undefined),
    laidOutCount: worldRects.size,
  };
}

/**
 * Relations per parent, strongest first. Only relations whose endpoints both
 * exist and are laid out are kept, so the renderer never dereferences a pruned
 * node. `Array.prototype.sort` is stable, so equal counts keep their wire order.
 */
function bucketRelations(
  relations: readonly ZoomRelation[],
  worldRects: Map<string, Rect>,
): Map<string, ZoomRelation[]> {
  const byParent = new Map<string, ZoomRelation[]>();
  for (const r of relations) {
    if (!worldRects.has(r.source_id) || !worldRects.has(r.target_id)) continue;
    const bucket = byParent.get(r.parent_id);
    if (bucket) bucket.push(r);
    else byParent.set(r.parent_id, [r]);
  }
  for (const bucket of byParent.values()) bucket.sort((a, b) => b.edge_count - a.edge_count);
  return byParent;
}

/** Fill the card footer badge slot (see `cardBadge`) for every node, once. */
function setContainerBadges(nodes: Map<string, ZoomNode>, list: readonly ZoomContainer[]): void {
  const containers = new Map(list.map((c) => [c.id, c] as const));
  for (const n of nodes.values()) {
    const parent = n.parent_id ? nodes.get(n.parent_id) : undefined;
    setCardBadge(n, containerBadge(n, parent, containers));
  }
}

function resolveDrawChildren(
  nodes: Map<string, ZoomNode>,
  worldRects: Map<string, Rect>,
): Map<string, readonly ZoomNode[]> {
  const out = new Map<string, readonly ZoomNode[]>();
  for (const id of worldRects.keys()) {
    const node = nodes.get(id);
    const kids = node ? laidOutChildren(nodes, worldRects, node) : [];
    if (kids.length === 0) continue;
    out.set(id, kids.length > MAX_CHILDREN_DRAWN ? selectChildren(kids, MAX_CHILDREN_DRAWN) : kids);
  }
  return out;
}

function laidOutChildren(
  nodes: Map<string, ZoomNode>,
  worldRects: Map<string, Rect>,
  node: ZoomNode,
): ZoomNode[] {
  const out: ZoomNode[] = [];
  for (const id of node.children) {
    const child = nodes.get(id);
    if (child && worldRects.has(id)) out.push(child);
  }
  return out;
}

const NO_CHILDREN: readonly ZoomNode[] = [];

/** A node's drawable children (see `ZoomScene.drawChildren`); shared, do not mutate. */
export function childNodes(scene: ZoomScene, node: ZoomNode): readonly ZoomNode[] {
  return scene.drawChildren.get(node.id) ?? NO_CHILDREN;
}
