/**
 * Pure layout + filtering for the Live System Map. Filters the system graph to
 * the visible edge kinds, optionally collapses to repos, then positions the
 * nodes with the shared ELK helper (reused from the C4 view — one layout
 * engine, no bespoke positioning). Returns plain data; the hook turns it into
 * React Flow nodes/edges.
 */

import { computeC4Layout, type C4LayoutPosition } from "../../c4/layout/elk-c4-layout";
import type { SystemEdgeKind, SystemGraph } from "@repowise-dev/types";
import { collapseToRepos } from "./collapse";

/** Uniform service-node footprint on the map. */
export const SYSTEM_MAP_NODE_SIZE = { width: 200, height: 84 } as const;

export interface SystemMapView {
  /** Edge kinds to keep; an edge survives only if its kind is in the set. */
  visibleKinds: ReadonlySet<SystemEdgeKind>;
  /** Collapse services into one node per repo. */
  collapsed: boolean;
}

/**
 * Apply the view's filters to a raw system graph. Pure: returns a new graph;
 * nodes are retained even when filtering leaves them edgeless (honest — an
 * isolated service is real signal, not noise to hide).
 */
export function applyView(graph: SystemGraph, view: SystemMapView): SystemGraph {
  const base = view.collapsed ? collapseToRepos(graph) : graph;
  const edges = base.edges.filter((e) => view.visibleKinds.has(e.kind));
  return { ...base, edges };
}

/** Position every node via the shared ELK layered layout. */
export async function computeSystemMapPositions(
  graph: SystemGraph,
): Promise<Map<string, C4LayoutPosition>> {
  return computeC4Layout(
    graph.nodes.map((n) => ({
      id: n.id,
      width: SYSTEM_MAP_NODE_SIZE.width,
      height: SYSTEM_MAP_NODE_SIZE.height,
    })),
    graph.edges.map((e) => ({ id: e.id, source: e.source, target: e.target })),
  );
}
