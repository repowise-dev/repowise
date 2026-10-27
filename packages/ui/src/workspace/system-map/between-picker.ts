/**
 * What the Between picker needs from the loaded graph: service suggestions
 * for a typed name, and whether two services are joined within two hops in
 * the families shown (`servicesBetween`). Pure, no React, no I/O; the graph
 * is already in the browser.
 */

import type { GraphPath, NodeSearchResult } from "@repowise-dev/types/graph";
import { servicesBetween } from "./between";
import type { EdgeFamily } from "./edge-families";
import type { LevelsGraph } from "./level-types";

function degrees(graph: LevelsGraph): Map<string, number> {
  const out = new Map<string, number>();
  for (const e of graph.edges) {
    out.set(e.source, (out.get(e.source) ?? 0) + 1);
    out.set(e.target, (out.get(e.target) ?? 0) + 1);
  }
  return out;
}

/**
 * Services whose name or id contains every typed term (case-insensitive),
 * names starting with the query first, then the most linked. An empty query
 * lists the most linked services. `language` carries the repository, which
 * the picker shows beside the name.
 */
export function matchServices(graph: LevelsGraph, query: string, limit: number): NodeSearchResult[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const degree = degrees(graph);
  const head = terms.join(" ");
  return graph.nodes
    .filter((n) => {
      const hay = `${n.name} ${n.id}`.toLowerCase();
      return terms.every((t) => hay.includes(t));
    })
    .map((n) => ({ n, prefix: head !== "" && n.name.toLowerCase().startsWith(head) ? 0 : 1, degree: degree.get(n.id) ?? 0 }))
    .sort((x, y) => x.prefix - y.prefix || y.degree - x.degree || (x.n.id < y.n.id ? -1 : 1))
    .slice(0, limit)
    .map(({ n }) => ({ node_id: n.id, language: n.repo, symbol_count: 0 }));
}

/** A typed value as a service id: an id, else an exact name (case-insensitive). Null when it names none. */
export function resolveService(graph: LevelsGraph, value: string): string | null {
  const v = value.trim();
  if (graph.nodes.some((n) => n.id === v)) return v;
  const lower = v.toLowerCase();
  return graph.nodes.find((n) => n.name.toLowerCase() === lower)?.id ?? null;
}

/**
 * The shortest route between two services within two hops, either way, in
 * the families shown, as the picker's path payload. An empty path when none
 * joins them; it throws when either value names no service.
 */
export function pickerPath(
  graph: LevelsGraph,
  from: string,
  to: string,
  families: ReadonlySet<EdgeFamily>,
): GraphPath {
  const a = resolveService(graph, from);
  const b = resolveService(graph, to);
  if (!a || !b) throw new Error("Not a service in this map");
  const between = servicesBetween(graph, a, b, { families, maxPaths: 1 });
  const forward = between?.pathsAToB[0];
  const backward = between?.pathsBToA[0];
  // Keep the reader's order: a route b -> a still answers "between a and b".
  const nodes = forward?.nodes ?? (backward ? [...backward.nodes].reverse() : []);
  return { path: nodes, distance: Math.max(nodes.length - 1, 0), explanation: "" };
}
