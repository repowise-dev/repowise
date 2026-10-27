/**
 * Bands: turn system edges into what the map draws, given where each service
 * is drawn. Two service cards keep their own edges (kind and match dash
 * intact); any pair where one end aggregates (a group card, the tray, a
 * "+N more" node) gets one band per ordered pair, carrying the link count and
 * the kind mix. Opposite directions are two bands, so a cycle stays visible.
 * Pure, no React.
 */

import { linkWeight } from "./edge-families";
import type { DrawnBand, DrawnEdge, LevelsEdge, LinkSummary } from "./level-types";

export function emptySummary(): LinkSummary {
  return { links: 0, edges: 0, kinds: {}, gatewayLinks: 0, edgeIds: [] };
}

/** Fold one edge into a summary, in place. */
export function addLink(summary: LinkSummary, edge: LevelsEdge): void {
  const w = linkWeight(edge);
  summary.links += w;
  summary.edges += 1;
  summary.kinds[edge.kind] = (summary.kinds[edge.kind] ?? 0) + w;
  if (edge.strategy === "gateway_route") summary.gatewayLinks += w;
  summary.edgeIds.push(edge.id);
}

export function summarizeLinks(edges: Iterable<LevelsEdge>): LinkSummary {
  const s = emptySummary();
  for (const e of edges) addLink(s, e);
  return s;
}

/** Ascending code-unit order, the one id order every view sorts by. */
export function compareIds(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

export function bandId(source: string, target: string): string {
  return `band:${source}>${target}`;
}

/**
 * Connect `edges` between drawn nodes. `drawnAt(serviceId)` names the node a
 * service is drawn as (or inside), null when it is not on the canvas;
 * `isServiceCard(drawnId)` is true when that node is the service's own card.
 * Links whose two ends land on one node are not drawn. Sorted by id.
 */
export function connectEdges(
  edges: Iterable<LevelsEdge>,
  drawnAt: (serviceId: string) => string | null,
  isServiceCard: (drawnId: string) => boolean,
): DrawnEdge[] {
  const singles: DrawnEdge[] = [];
  const bands = new Map<string, DrawnBand>();
  for (const e of edges) {
    const source = drawnAt(e.source);
    const target = drawnAt(e.target);
    if (source === null || target === null) continue;
    if (source === target) continue;
    if (isServiceCard(source) && isServiceCard(target)) {
      singles.push({ type: "edge", id: e.id, source, target, edgeId: e.id });
      continue;
    }
    const id = bandId(source, target);
    let band = bands.get(id);
    if (!band) bands.set(id, (band = { type: "band", id, source, target, summary: emptySummary() }));
    addLink(band.summary, e);
  }
  const out = [...singles, ...bands.values()];
  out.sort((x, y) => compareIds(x.id, y.id));
  return out;
}
