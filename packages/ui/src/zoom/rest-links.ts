/**
 * Which relations a level draws at rest, before anything is hovered. Pure,
 * browser-free.
 *
 * A level with no arrows until you hover says nothing about how its parts
 * depend on each other, so the level the camera looks into draws its
 * strongest few relations faintly. Links touching the test suite are left out
 * by default: on this repository 6 of the 10 strongest top-level relations are
 * tests calling the code they test, which would crowd out every production
 * dependency. Inside a test subtree they are the level's own links and draw.
 */

import type { ZoomMap, ZoomNode, ZoomRelation } from "./types";

/** Relations a level draws at rest: enough to show its spine, few enough to stay calm. */
export const REST_EDGE_MAX = 8;

/**
 * Stroke width by `edge_count`, three steps: under 300, 300 to 1,000, over
 * 1,000 file-pair edges. Absolute rather than per level so a thick line means
 * the same thing at every depth; deep levels simply read thin.
 */
const EDGE_STEP_MIN = [300, 1000] as const;
const EDGE_STEP_PX = [1.25, 2, 3] as const;

export function edgeStrokePx(edgeCount: number): number {
  if (edgeCount > EDGE_STEP_MIN[1]) return EDGE_STEP_PX[2];
  if (edgeCount >= EDGE_STEP_MIN[0]) return EDGE_STEP_PX[1];
  return EDGE_STEP_PX[0];
}

/** Per node map: is each node's subtree mostly test files. */
const testMemo = new WeakMap<ReadonlyMap<string, ZoomNode>, Map<string, boolean>>();

/** [files, test files] under `id`. */
function countTests(nodes: ReadonlyMap<string, ZoomNode>, id: string): [number, number] {
  const n = nodes.get(id);
  if (!n) return [0, 0];
  if (n.kind === "file") return [1, n.is_test ? 1 : 0];
  let files = 0;
  let tests = 0;
  for (const c of n.children) {
    const [f, t] = countTests(nodes, c);
    files += f;
    tests += t;
  }
  return [files, tests];
}

/**
 * A file flagged `is_test`, or a container most of whose files are. Only files
 * carry the flag; a curated "tests" layer is recognised by what it holds, not
 * by its (model-written) name.
 */
export function isTestSubtree(nodes: ReadonlyMap<string, ZoomNode>, id: string): boolean {
  let memo = testMemo.get(nodes);
  if (!memo) testMemo.set(nodes, (memo = new Map()));
  let hit = memo.get(id);
  if (hit === undefined) {
    const [files, tests] = countTests(nodes, id);
    memo.set(id, (hit = tests * 2 > files));
  }
  return hit;
}

/** Is `r` a test link the at-rest set hides (see the module comment)? */
export function isHiddenTestLink(nodes: ReadonlyMap<string, ZoomNode>, r: ZoomRelation): boolean {
  if (isTestSubtree(nodes, r.parent_id)) return false;
  return isTestSubtree(nodes, r.source_id) || isTestSubtree(nodes, r.target_id);
}

export interface RestLinkQuery {
  relationVerbs: ReadonlySet<string> | null;
  showTestLinks: boolean;
}

export interface RestLinkSummary {
  /** Relations the level could draw at rest, at most `REST_EDGE_MAX`. */
  shown: number;
  /** Relations among the level's children that pass the verb filter. */
  total: number;
}

const nodesByMap = new WeakMap<ZoomMap, Map<string, ZoomNode>>();

/** `map`'s nodes by id, memoized per map so `isTestSubtree`'s memo is shared. */
export function mapNodes(map: ZoomMap): ReadonlyMap<string, ZoomNode> {
  let nodes = nodesByMap.get(map);
  if (!nodes) {
    nodes = new Map(map.nodes.map((n) => [n.id, n]));
    nodesByMap.set(map, nodes);
  }
  return nodes;
}

/**
 * What the map key says about the at-rest arrows of `parentId`'s level. An
 * upper bound: the canvas also skips links whose cards are too small on screen
 * to anchor an arrow.
 */
export function restLinkSummary(map: ZoomMap, parentId: string, q: RestLinkQuery): RestLinkSummary {
  const nodes = mapNodes(map);
  let total = 0;
  let eligible = 0;
  for (const r of map.relations) {
    if (r.parent_id !== parentId || r.source_id === r.target_id) continue;
    if (q.relationVerbs !== null && !q.relationVerbs.has(r.label)) continue;
    total++;
    if (q.showTestLinks || !isHiddenTestLink(nodes, r)) eligible++;
  }
  return { shown: Math.min(eligible, REST_EDGE_MAX), total };
}
