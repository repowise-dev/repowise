/**
 * Between two services: the links that join them directly, in both
 * directions, and when there is no direct link the shortest paths through one
 * other service ("webhookclient reaches catalog.api through webhooks.api").
 * Paths and direct links follow the map's active families (Runtime and Data
 * by default), so what this counts matches what the map draws; a package or
 * co-change edge is a route only when the reader turned its family on. The
 * server's routes-between totals count every kind; this view filters them. Pure, no React; the graph is already loaded.
 */

import { addLink, compareIds, emptySummary } from "./bands";
import { DEFAULT_EDGE_FAMILIES, edgeFamilyOf, type EdgeFamily } from "./edge-families";
import type { LevelsEdge, LevelsGraph, LinkSummary } from "./level-types";

export interface PathHop {
  source: string;
  target: string;
  summary: LinkSummary;
}

/** One route from `nodes[0]` to its last node; `hops[i]` joins `nodes[i]` to `nodes[i + 1]`. */
export interface ServicePath {
  nodes: string[];
  hops: PathHop[];
}

export interface BetweenResult {
  a: string;
  b: string;
  /** Direct links, by direction; null when there is none that way. */
  aToB: LinkSummary | null;
  bToA: LinkSummary | null;
  /** Shortest paths each way: the direct link when there is one, else up to `maxPaths` through one service (strongest first). */
  pathsAToB: ServicePath[];
  pathsBToA: ServicePath[];
}

export interface BetweenOptions {
  /** Families walked for paths and counted as direct links: the map's active families, Runtime and Data by default. */
  families?: ReadonlySet<EdgeFamily>;
  /** 1 = direct only, 2 = also through one other service. Default 2. */
  maxHops?: 1 | 2;
  /** Paths kept per direction. Default 3. */
  maxPaths?: number;
}

type Adjacency = Map<string, Map<string, LinkSummary>>;

/** source -> target -> summary of every edge in the families, self-edges dropped. */
export function buildAdjacency(edges: Iterable<LevelsEdge>, families: ReadonlySet<EdgeFamily>): Adjacency {
  const adj: Adjacency = new Map();
  for (const e of edges) {
    if (e.source === e.target || !families.has(edgeFamilyOf(e))) continue;
    let out = adj.get(e.source);
    if (!out) adj.set(e.source, (out = new Map()));
    let s = out.get(e.target);
    if (!s) out.set(e.target, (s = emptySummary()));
    addLink(s, e);
  }
  return adj;
}

/** Up to `maxPaths` shortest simple paths from `from` to `to`, at most `maxHops` long. */
export function shortestPaths(
  adj: Adjacency,
  from: string,
  to: string,
  maxHops: 1 | 2 = 2,
  maxPaths = 3,
): ServicePath[] {
  const out = from === to || maxPaths <= 0 ? undefined : adj.get(from);
  if (!out) return [];
  const direct = out.get(to);
  if (direct) return [{ nodes: [from, to], hops: [{ source: from, target: to, summary: direct }] }];
  if (maxHops < 2) return [];
  return throughOne(adj, out, from, to).slice(0, maxPaths);
}

/** Paths `from -> via -> to`, strongest first: a path is as strong as its weakest hop, ties by id. */
function throughOne(adj: Adjacency, out: Map<string, LinkSummary>, from: string, to: string): ServicePath[] {
  const twoHop: { via: string; strength: number; first: LinkSummary; second: LinkSummary }[] = [];
  for (const [via, first] of out) {
    const second = via === to ? undefined : adj.get(via)?.get(to);
    if (second) twoHop.push({ via, strength: Math.min(first.links, second.links), first, second });
  }
  twoHop.sort((x, y) => y.strength - x.strength || compareIds(x.via, y.via));
  return twoHop.map(({ via, first, second }) => ({
    nodes: [from, via, to],
    hops: [
      { source: from, target: via, summary: first },
      { source: via, target: to, summary: second },
    ],
  }));
}

/** Links and paths between `a` and `b`. Null when either id is not a service in the graph or they are the same. */
export function servicesBetween(
  graph: LevelsGraph,
  a: string,
  b: string,
  options: BetweenOptions = {},
): BetweenResult | null {
  if (a === b) return null;
  const ids = new Set(graph.nodes.map((n) => n.id));
  if (!ids.has(a) || !ids.has(b)) return null;
  const adj = buildAdjacency(graph.edges, options.families ?? DEFAULT_EDGE_FAMILIES);
  const maxHops = options.maxHops ?? 2;
  const maxPaths = options.maxPaths ?? 3;
  return {
    a,
    b,
    aToB: adj.get(a)?.get(b) ?? null,
    bToA: adj.get(b)?.get(a) ?? null,
    pathsAToB: shortestPaths(adj, a, b, maxHops, maxPaths),
    pathsBToA: shortestPaths(adj, b, a, maxHops, maxPaths),
  };
}
