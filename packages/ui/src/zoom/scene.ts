/**
 * Pure scene model derived from the fetched zoom map. Browser-free.
 *
 * Indexes the flat node list by id, precomputes every node's absolute world
 * rect (see `geometry.ts`) and buckets relations by their `parent_id` so the
 * renderer can pull just the relations relevant to whichever node it has zoomed
 * into. Rebuilt only when a new map arrives (or a deeper subtree is fetched),
 * never per frame.
 */

import type { Rect } from "./camera";
import { computeWorldRects } from "./geometry";
import type { GridLayoutOptions } from "./layout";
import type { ZoomMap, ZoomNode, ZoomRelation } from "./types";

export interface ZoomScene {
  rootId: string;
  nodes: Map<string, ZoomNode>;
  worldRects: Map<string, Rect>;
  relationsByParent: Map<string, ZoomRelation[]>;
  /** Total nodes that received a world rect (reachable, laid-out). */
  laidOutCount: number;
}

export function buildScene(map: ZoomMap, layout: GridLayoutOptions = {}): ZoomScene {
  const nodes = new Map<string, ZoomNode>();
  for (const n of map.nodes) nodes.set(n.id, n);

  const worldRects = computeWorldRects(nodes, map.root_id, layout);

  const relationsByParent = new Map<string, ZoomRelation[]>();
  for (const r of map.relations) {
    // Only keep relations whose endpoints both exist and are laid out, so the
    // renderer never dereferences a pruned node.
    if (!worldRects.has(r.source_id) || !worldRects.has(r.target_id)) continue;
    const bucket = relationsByParent.get(r.parent_id);
    if (bucket) bucket.push(r);
    else relationsByParent.set(r.parent_id, [r]);
  }

  return {
    rootId: map.root_id,
    nodes,
    worldRects,
    relationsByParent,
    laidOutCount: worldRects.size,
  };
}

/** Resolve a node's children to node objects, skipping any missing/unlaid-out. */
export function childNodes(scene: ZoomScene, node: ZoomNode): ZoomNode[] {
  const out: ZoomNode[] = [];
  for (const id of node.children) {
    const child = scene.nodes.get(id);
    if (child && scene.worldRects.has(id)) out.push(child);
  }
  return out;
}
