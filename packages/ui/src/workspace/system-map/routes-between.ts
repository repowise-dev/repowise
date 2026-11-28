/**
 * Routes between two services, as plain data for the rail: which kinds the
 * map's active families keep (the server's totals count every kind), how many
 * rows each kind holds per direction, what is left out and why, the lede
 * sentences, and one line of text per row. Pure, no React, no I/O; the rows
 * come from the host's `loadLinksBetween`.
 */

import type {
  ServiceLinkDirection,
  ServiceLinkEntry,
  ServiceLinksBetween,
  ServiceLinksTotal,
} from "@repowise-dev/types/workspace";
import { kindMixText } from "./aggregate-facts";
import type { BetweenResult, ServicePath } from "./between";
import { EDGE_FAMILY_ORDER, edgeFamily, type EdgeFamily } from "./edge-families";
import { EDGE_KIND_ORDER, edgeKindStyle } from "./edge-kinds";
import type { LinkSummary } from "./level-types";
import { plural } from "./system-map-model";

/** Options a host's loader takes; mirrors `GET /api/workspace/system-graph/links`. */
export interface LinksBetweenOptions {
  kind?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

/** The host's loader: one page of rows between `a` and `b`. */
export type LoadLinksBetween = (a: string, b: string, opts: LinksBetweenOptions) => Promise<ServiceLinksBetween>;

/** Rows per request in the routes panel. */
export const ROUTES_PAGE = 50;

/** Display order of directions inside a kind. */
export const DIRECTION_ORDER: readonly ServiceLinkDirection[] = ["a_to_b", "b_to_a", "both"];

export interface RouteKindSection {
  kind: string;
  /** Rows of this kind, every direction, under the filter. */
  total: number;
  byDirection: Partial<Record<ServiceLinkDirection, number>>;
}

export interface HiddenFamily {
  family: EdgeFamily;
  rows: number;
}

export interface RoutesPlan {
  /** Kinds the map's families keep, in legend order, each with its rows. */
  sections: RouteKindSection[];
  /** Rows the families keep. */
  shown: number;
  /** Rows in families the map hides, per family, in chip order. */
  hidden: HiddenFamily[];
}

function kindRank(kind: string): number {
  const i = EDGE_KIND_ORDER.indexOf(kind as never);
  return i === -1 ? EDGE_KIND_ORDER.length : i;
}

/** Split the server's totals into the kinds listed (in the active families) and the rows each hidden family holds. */
export function planRoutes(totals: readonly ServiceLinksTotal[], families: ReadonlySet<EdgeFamily>): RoutesPlan {
  const byKind = new Map<string, RouteKindSection>();
  const hidden = new Map<EdgeFamily, number>();
  for (const t of totals) {
    if (t.count <= 0) continue;
    const family = edgeFamily(t.kind);
    if (!families.has(family)) {
      hidden.set(family, (hidden.get(family) ?? 0) + t.count);
      continue;
    }
    let s = byKind.get(t.kind);
    if (!s) byKind.set(t.kind, (s = { kind: t.kind, total: 0, byDirection: {} }));
    s.total += t.count;
    s.byDirection[t.direction] = (s.byDirection[t.direction] ?? 0) + t.count;
  }
  const sections = [...byKind.values()].sort((x, y) => kindRank(x.kind) - kindRank(y.kind) || (x.kind < y.kind ? -1 : 1));
  return {
    sections,
    shown: sections.reduce((n, s) => n + s.total, 0),
    hidden: EDGE_FAMILY_ORDER.filter((f) => hidden.has(f)).map((family) => ({ family, rows: hidden.get(family)! })),
  };
}

/** Loaded rows of one kind, grouped by direction in display order; empty directions are left out. */
export function rowsByDirection(rows: readonly ServiceLinkEntry[]): { direction: ServiceLinkDirection; rows: ServiceLinkEntry[] }[] {
  return DIRECTION_ORDER.map((direction) => ({ direction, rows: rows.filter((r) => r.direction === direction) })).filter(
    (g) => g.rows.length > 0,
  );
}

/** "webapp to ordering.api", "ordering.api to webapp", or "Changed together" for co-change rows. */
export function directionLabel(direction: ServiceLinkDirection, aName: string, bName: string): string {
  if (direction === "a_to_b") return `${aName} to ${bName}`;
  if (direction === "b_to_a") return `${bName} to ${aName}`;
  return "Changed together";
}

function oneWay(from: string, to: string, summary: LinkSummary | null): string {
  if (!summary || summary.links === 0) return `Nothing in ${from} depends on ${to}.`;
  return `${from} depends on ${to} through ${plural(summary.links, "link", "links")} (${kindMixText(summary.kinds)}).`;
}

/** "HTTP and Event": a hop's kinds in legend order, without counts. */
function kindWords(summary: LinkSummary): string {
  return Object.keys(summary.kinds)
    .filter((k) => (summary.kinds[k] ?? 0) > 0)
    .sort((x, y) => kindRank(x) - kindRank(y) || (x < y ? -1 : 1))
    .map((k) => edgeKindStyle(k).label)
    .join(" and ");
}

function pathText(path: ServicePath, name: (id: string) => string): string {
  const kinds = path.hops.map((h) => kindWords(h.summary)).join(", then ");
  return `${name(path.nodes[0]!)} reaches ${name(path.nodes[path.nodes.length - 1]!)} through ${path.nodes
    .slice(1, -1)
    .map(name)
    .join(", ")} (${kinds}).`;
}

/**
 * The lede, from the graph the map drew (the families shown): each direction
 * in one sentence, or, with no direct link either way, the shortest paths
 * through one other service, or that there is none.
 */
export function routesLede(between: BetweenResult, name: (id: string) => string): string[] {
  const a = name(between.a);
  const b = name(between.b);
  if (between.aToB || between.bToA) return [oneWay(a, b, between.aToB), oneWay(b, a, between.bToA)];
  const paths = [...between.pathsAToB, ...between.pathsBToA];
  const head = `No direct link between ${a} and ${b} in the links shown.`;
  if (paths.length === 0) return [head, "No path joins them through one other service either."];
  return [head, ...paths.map((p) => pathText(p, name))];
}

/** The hidden-family note: "Not listed: 3 Build rows (package dependencies)." Null when nothing is hidden. */
export function hiddenRoutesSentence(hidden: readonly HiddenFamily[], copy: Record<EdgeFamily, { label: string; meaning: string }>): string | null {
  if (hidden.length === 0) return null;
  const parts = hidden.map((h) => `${plural(h.rows, `${copy[h.family].label} row`, `${copy[h.family].label} rows`)} (${copy[h.family].meaning})`);
  return `Not listed, because the map hides their family: ${parts.join(", ")}.`;
}

/** A row's one-line name: the route, the package, or the file pair. */
export function routeTitle(row: ServiceLinkEntry): string {
  if (row.link) return row.link.contract_id;
  if (row.package_dep) {
    const d = row.package_dep;
    return d.requested_version ? `${d.target_package} ${d.requested_version}` : d.target_package;
  }
  if (row.co_change) return `${row.co_change.source_file} with ${row.co_change.target_file}`;
  return edgeKindStyle(row.kind).label;
}

/** A stable key for a row: its evidence and both ends. */
export function routeKey(row: ServiceLinkEntry): string {
  const l = row.link;
  if (l) return `c|${row.direction}|${l.contract_id}|${l.provider_repo}/${l.provider_file}|${l.consumer_repo}/${l.consumer_file}`;
  const d = row.package_dep;
  if (d) return `p|${row.direction}|${d.source_repo}/${d.source_manifest}|${d.target_package}`;
  const c = row.co_change;
  if (c) return `h|${c.source_repo}/${c.source_file}|${c.target_repo}/${c.target_file}`;
  return `${row.kind}|${row.direction}`;
}

/** True when a response carries the graph-older-than-contracts flag. */
export function isStale(page: Pick<ServiceLinksBetween, "stale_graph"> | null | undefined): boolean {
  return page?.stale_graph === true;
}
