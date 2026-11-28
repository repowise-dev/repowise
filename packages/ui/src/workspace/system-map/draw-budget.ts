/**
 * The draw budget: at most ~120 nodes and ~300 edges on the canvas at once.
 * Past it, the weakest services of each container fold into one "+N more"
 * node and the weakest connectors are not drawn; both are counted in the
 * truncation report so the caption can say so. Pure, no React.
 */

import { linkWeight } from "./edge-families";
import type { DrawBudget, DrawnEdge, LevelsEdge, TruncationReport } from "./level-types";

/** React Flow stays responsive with this much drawn. */
export const DRAW_BUDGET: DrawBudget = { nodes: 120, edges: 300 };

export interface BudgetContainer {
  /** Items the container would draw. */
  size: number;
  /** Most items it may draw before folding, whatever the budget (a large open group). */
  limit?: number;
}

/**
 * Items each container keeps so that kept items plus one "+N more" node per
 * folded container fit `capacity`. Water-filling: every container keeps up to
 * one shared cap, chosen as large as fits, so a small container is never
 * folded to make room for a big one; slots the shared cap leaves over go to
 * the first containers still folding. Deterministic. When even one "+N more"
 * per container does not fit, every container keeps nothing and the view
 * draws more than the budget (only reachable with more open groups than slots).
 */
export function allocateSlots(containers: readonly BudgetContainer[], capacity: number): number[] {
  const keptAt = (cap: number) =>
    containers.map((c) => Math.min(c.size, c.limit ?? c.size, cap));
  const cost = (cap: number) =>
    keptAt(cap).reduce((sum, kept, i) => sum + kept + (kept < containers[i]!.size ? 1 : 0), 0);
  let lo = 0;
  let hi = containers.reduce((m, c) => Math.max(m, c.size), 0);
  // cost() never decreases as the cap grows, so binary-search the largest cap that fits.
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (cost(mid) <= capacity) lo = mid;
    else hi = mid - 1;
  }
  const kept = keptAt(lo);
  let spare = Math.max(0, capacity - cost(lo));
  containers.forEach((c, i) => {
    const max = Math.min(c.size, c.limit ?? c.size);
    const room = max - kept[i]!;
    if (room <= 0) return;
    // Filling a container whole also frees its "+N more" slot.
    if (max === c.size && room <= spare + 1) {
      kept[i] = max;
      spare -= room - 1;
    } else {
      const give = Math.min(room, spare);
      kept[i]! += give;
      spare -= give;
    }
  });
  return kept;
}

export interface CappedEdges {
  edges: DrawnEdge[];
  droppedEdges: number;
  droppedLinks: number;
}

/** Links a drawn connector stands for: a band's count, or one edge's weight. */
export function drawnLinks(edge: DrawnEdge, edgesById: ReadonlyMap<string, LevelsEdge>): number {
  if (edge.type === "band") return edge.summary.links;
  const e = edgesById.get(edge.edgeId);
  return e ? linkWeight(e) : 1;
}

/** Keep the `max` strongest connectors (most links, then id), in their original order. */
export function capEdges(
  edges: readonly DrawnEdge[],
  max: number,
  edgesById: ReadonlyMap<string, LevelsEdge>,
): CappedEdges {
  if (edges.length <= max) return { edges: [...edges], droppedEdges: 0, droppedLinks: 0 };
  const ranked = edges
    .map((edge) => ({ edge, links: drawnLinks(edge, edgesById) }))
    .sort((a, b) => b.links - a.links || (a.edge.id < b.edge.id ? -1 : a.edge.id > b.edge.id ? 1 : 0));
  const keep = new Set(ranked.slice(0, Math.max(0, max)).map((r) => r.edge.id));
  let droppedLinks = 0;
  for (const r of ranked.slice(Math.max(0, max))) droppedLinks += r.links;
  return {
    edges: edges.filter((e) => keep.has(e.id)),
    droppedEdges: edges.length - keep.size,
    droppedLinks,
  };
}

/** The report for what a view folded and dropped; null when everything fit. */
export function truncationReport(
  budget: DrawBudget,
  foldedServices: number,
  foldedGroups: number,
  droppedEdges: number,
  droppedLinks: number,
): TruncationReport | null {
  if (foldedServices + foldedGroups + droppedEdges === 0) return null;
  return { budget, foldedServices, foldedGroups, droppedEdges, droppedLinks };
}
