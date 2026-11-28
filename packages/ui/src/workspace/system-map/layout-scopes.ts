/**
 * What the levels layout hands to ELK, as plain data. A drawn view splits into
 * scopes: one per open container (an opened group or the open tray), holding
 * its own cards, and the root, holding everything else with each open
 * container as one box. Each scope is laid out on its own and cached by its
 * signature, so opening a group lays out that group's interior once and
 * re-runs only the root with the group's box at its new size.
 *
 * Placement keys on every edge family but History, whatever the family
 * filter draws, so a filter toggle never moves a card (the drawn node set is
 * filter-independent by construction in `estate-view.ts`). Pure, no React.
 */

import type { C4LayoutEdge, C4LayoutNode, C4LayoutPosition } from "../../c4/layout/elk-c4-layout";
import { compareIds } from "./bands";
import { edgeFamilyOf, linkWeight } from "./edge-families";
import type { DrawnNode, LevelsEdge } from "./level-types";

interface Size {
  width: number;
  height: number;
}

/** A service card's footprint on the map. */
export const SYSTEM_MAP_NODE_SIZE = { width: 208, height: 96 } as const;

/** Card footprints per drawn node type. Open containers are sized from their interior. */
export interface LevelNodeSizes {
  service: Size;
  group: Size;
  tray: Size;
  more: Size;
}

export const LEVEL_NODE_SIZES: LevelNodeSizes = {
  service: SYSTEM_MAP_NODE_SIZE,
  group: { width: 240, height: 104 },
  tray: { width: 240, height: 104 },
  more: SYSTEM_MAP_NODE_SIZE,
};

/** Room an open container keeps around its cards; the top holds its title row. */
export const CONTAINER_PADDING = { top: 48, right: 24, bottom: 24, left: 24 } as const;

export const ROOT_SCOPE = "root";

export interface LayoutScope {
  /** `ROOT_SCOPE`, or the drawn id of the open container whose interior this is. */
  id: string;
  /** In drawn order. In the root, an open container has a zero size until its interior is laid out. */
  nodes: C4LayoutNode[];
  /** Placement edges: at most one per pair of drawn ids, the strongest kept, in id order. */
  edges: C4LayoutEdge[];
}

export interface LevelLayoutPlan {
  interiors: LayoutScope[];
  root: LayoutScope;
}

function isOpenContainer(n: DrawnNode): boolean {
  return (n.type === "group" || n.type === "tray") && n.open;
}

function nodeSize(n: DrawnNode, sizes: LevelNodeSizes): Size {
  if (isOpenContainer(n)) return { width: 0, height: 0 };
  return sizes[n.type];
}

/** Every family but History shapes placement; co-change is undirected and would add layers. */
function shapesLayout(e: LevelsEdge): boolean {
  return e.source !== e.target && edgeFamilyOf(e) !== "history";
}

interface Placement {
  /** Drawn id -> the open container it sits in. */
  parentOf: Map<string, string>;
  /** Service id -> the drawn id it is shown at. */
  drawnAt: Map<string, string>;
}

/** Where each service is drawn: its own card, else the closed group, tray or "+N more" holding it. */
function placement(nodes: readonly DrawnNode[]): Placement {
  const parentOf = new Map<string, string>();
  const drawnAt = new Map<string, string>();
  for (const n of nodes) {
    if ((n.type === "service" || n.type === "more") && n.parentId) parentOf.set(n.id, n.parentId);
    if (n.type === "service") drawnAt.set(n.serviceId, n.id);
  }
  for (const n of nodes) {
    if (n.type === "service" || isOpenContainer(n)) continue;
    for (const id of n.serviceIds) if (!drawnAt.has(id)) drawnAt.set(id, n.id);
  }
  return { parentOf, drawnAt };
}

/**
 * The scope and drawn ends an edge shapes: inside one container when both
 * ends sit in it, else the root between their top-level boxes. Null when
 * the edge joins a box to itself or an end is not drawn.
 */
function liftEdge(e: LevelsEdge, { parentOf, drawnAt }: Placement): [string, string, string] | null {
  const s = drawnAt.get(e.source);
  const t = drawnAt.get(e.target);
  if (s === undefined || t === undefined || s === t) return null;
  const ps = parentOf.get(s);
  const pt = parentOf.get(t);
  if (ps === pt) return [ps ?? ROOT_SCOPE, s, t];
  const [rs, rt] = [ps ?? s, pt ?? t];
  return rs === rt ? null : [ROOT_SCOPE, rs, rt];
}

/**
 * A scope's placement-edge budget per card, on average: the scope keeps at
 * most this times its card count, and a hub card may hold more than its
 * share. ELK's layered layout grows with edges far faster than with cards:
 * a 30-card estate over a 3,000-edge graph lifts to about 750 card pairs,
 * which took ELK about 50 s; capped at 60 edges it is well under 100 ms.
 */
export const PLACEMENT_EDGES_PER_CARD = 2;

/**
 * One edge per unordered card pair, pointing the way most links go (ties to
 * the lower id first). Every card first keeps its strongest pair, so no
 * linked card is left edgeless in layer 0 (a hub may take several of these);
 * the rest of a budget of `perCard` times the card count goes to the strongest remaining pairs, ties by id.
 * Layers follow the kept edges only, so a weak drawn connector may run
 * against them. Deterministic and independent of input order.
 */
export function placementEdges(
  weights: ReadonlyMap<string, { source: string; target: string; links: number }>,
  cards: number,
  perCard: number = PLACEMENT_EDGES_PER_CARD,
): C4LayoutEdge[] {
  const pairs = new Map<string, { source: string; target: string; links: number; net: number }>();
  for (const w of weights.values()) {
    const forward = compareIds(w.source, w.target) < 0;
    const key = forward ? `${w.source}\u0000${w.target}` : `${w.target}\u0000${w.source}`;
    let p = pairs.get(key);
    if (!p) {
      const [a, b] = forward ? [w.source, w.target] : [w.target, w.source];
      pairs.set(key, (p = { source: a, target: b, links: 0, net: 0 }));
    }
    p.links += w.links;
    p.net += forward ? w.links : -w.links;
  }
  const ranked = [...pairs.values()].map((p) => {
    const [source, target] = p.net >= 0 ? [p.source, p.target] : [p.target, p.source];
    return { id: `${source}>${target}`, source, target, links: p.links };
  });
  ranked.sort((x, y) => y.links - x.links || compareIds(x.id, y.id));
  const kept = new Set<(typeof ranked)[number]>();
  const linked = new Set<string>();
  for (const r of ranked) {
    if (linked.has(r.source) && linked.has(r.target)) continue;
    kept.add(r);
    linked.add(r.source).add(r.target);
  }
  const budget = Math.ceil(cards * perCard);
  for (const r of ranked) {
    if (kept.size >= budget) break;
    kept.add(r);
  }
  return [...kept].map(({ id, source, target }) => ({ id, source, target })).sort((x, y) => compareIds(x.id, y.id));
}

type PairWeights = Map<string, { source: string; target: string; links: number }>;

/**
 * Scopes for an estate, group or between view (focus uses fixed columns).
 * `edges` are the graph's system edges, each lifted to the drawn ids that
 * show its ends and summed per card pair (see `placementEdges`).
 */
export function planLevelLayout(
  nodes: readonly DrawnNode[],
  edges: readonly LevelsEdge[],
  sizes: LevelNodeSizes = LEVEL_NODE_SIZES,
): LevelLayoutPlan {
  const placed = placement(nodes);
  const scopes = new Map<string, { nodes: C4LayoutNode[]; weights: PairWeights }>();
  const scope = (id: string) => {
    let s = scopes.get(id);
    if (!s) scopes.set(id, (s = { nodes: [], weights: new Map() }));
    return s;
  };
  scope(ROOT_SCOPE);
  for (const n of nodes) {
    scope(placed.parentOf.get(n.id) ?? ROOT_SCOPE).nodes.push({ id: n.id, ...nodeSize(n, sizes) });
  }
  for (const e of edges) {
    const lifted = shapesLayout(e) ? liftEdge(e, placed) : null;
    if (!lifted) continue;
    const [scopeId, source, target] = lifted;
    const weights = scope(scopeId).weights;
    const id = `${source}>${target}`;
    const w = weights.get(id);
    if (w) w.links += linkWeight(e);
    else weights.set(id, { source, target, links: linkWeight(e) });
  }

  const toScope = (id: string): LayoutScope => {
    const s = scopes.get(id)!;
    return { id, nodes: s.nodes, edges: placementEdges(s.weights, s.nodes.length) };
  };
  const interiors = [...scopes.keys()].filter((id) => id !== ROOT_SCOPE && scopes.get(id)!.nodes.length > 0);
  return { interiors: interiors.map(toScope), root: toScope(ROOT_SCOPE) };
}

/** Identity of a scope's layout: its node footprints and edges, independent of order. */
export function scopeSignature(scope: LayoutScope): string {
  const nodes = scope.nodes.map((n) => `${n.id}:${n.width}x${n.height}`).sort();
  const edges = scope.edges.map((e) => e.id).sort();
  return `${nodes.join("|")}#${edges.join("|")}`;
}

/** One key for a whole plan; equal keys lay out to equal positions. */
export function planSignature(plan: LevelLayoutPlan): string {
  return [plan.root, ...plan.interiors].map((s) => `${s.id}=${scopeSignature(s)}`).join("\n");
}

/**
 * Nodes that were drawn before go first, in their previous reading order (row,
 * then column), and new ones follow in drawn order. Fed to ELK with
 * `considerModelOrder`, this keeps neighbours in their order when a group opens.
 */
export function orderByPrevious(
  nodes: readonly C4LayoutNode[],
  previous: ReadonlyMap<string, { x: number; y: number }> | undefined,
): C4LayoutNode[] {
  if (!previous || previous.size === 0) return [...nodes];
  const seen = nodes.filter((n) => previous.has(n.id));
  const fresh = nodes.filter((n) => !previous.has(n.id));
  seen.sort((a, b) => {
    const pa = previous.get(a.id)!;
    const pb = previous.get(b.id)!;
    return pa.y - pb.y || pa.x - pb.x || compareIds(a.id, b.id);
  });
  return [...seen, ...fresh];
}

export interface FittedContainer {
  /** Child positions relative to the container's top-left, as React Flow expects for `parentId`. */
  children: Map<string, C4LayoutPosition>;
  width: number;
  height: number;
}

/** Shift an interior layout into its container's padding and size the container to fit. */
export function fitContainer(positions: ReadonlyMap<string, C4LayoutPosition>): FittedContainer {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of positions.values()) {
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + p.width);
    maxY = Math.max(maxY, p.y + p.height);
  }
  const pad = CONTAINER_PADDING;
  const children = new Map<string, C4LayoutPosition>();
  if (positions.size === 0) return { children, width: pad.left + pad.right, height: pad.top + pad.bottom };
  for (const [id, p] of positions) {
    children.set(id, { ...p, x: p.x - minX + pad.left, y: p.y - minY + pad.top });
  }
  return { children, width: maxX - minX + pad.left + pad.right, height: maxY - minY + pad.top + pad.bottom };
}
