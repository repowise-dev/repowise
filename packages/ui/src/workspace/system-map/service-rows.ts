/**
 * The services table behind the map's List view: one row per service with
 * its group, its repository's health, its runtime links in and out, and its
 * contract counts; plus the table's sort. The text alternative to the canvas,
 * so it lists every service whatever level or family filter the map is on.
 * Pure, no React.
 */

import type { SystemEdge, SystemGraph } from "@repowise-dev/types";
import { edgeFamilyOf, linkWeight } from "./edge-families";
import type { RepoHealth } from "./types";

export interface ServiceRow {
  id: string;
  name: string;
  /** Where it lives: the repo, plus the service path when it has one. */
  location: string;
  /** Group label, null when the estate is not grouped or the service is in none. */
  group: string | null;
  /** 0-10 health of the service's repository; null when unscored. */
  health: number | null;
  /** Runtime links (calls and events) into and out of the service. */
  runtimeIn: number;
  runtimeOut: number;
  provided: number;
  consumed: number;
}

export type ServiceSortKey = "name" | "group" | "health" | "in" | "out" | "contracts";
export type SortOrder = "asc" | "desc";
export interface ServiceSort {
  key: ServiceSortKey;
  order: SortOrder;
}

export const DEFAULT_SERVICE_SORT: ServiceSort = { key: "name", order: "asc" };

type Edge = Pick<SystemEdge, "source" | "target" | "kind" | "weight" | "family">;

function runtimeLinks(edges: readonly Edge[]): { into: Map<string, number>; out: Map<string, number> } {
  const into = new Map<string, number>();
  const out = new Map<string, number>();
  for (const e of edges) {
    if (e.source === e.target || edgeFamilyOf(e) !== "runtime") continue;
    const w = linkWeight(e);
    out.set(e.source, (out.get(e.source) ?? 0) + w);
    into.set(e.target, (into.get(e.target) ?? 0) + w);
  }
  return { into, out };
}

export function serviceRows(
  graph: Pick<SystemGraph, "nodes" | "edges" | "groups">,
  healthByRepo?: ReadonlyMap<string, RepoHealth>,
): ServiceRow[] {
  const { into, out } = runtimeLinks(graph.edges);
  const groupLabel = new Map((graph.groups ?? []).map((g) => [g.id, g.label]));
  return graph.nodes.map((n) => {
    const h = healthByRepo?.get(n.repo);
    return {
      id: n.id,
      name: n.name,
      location: n.service_path ? `${n.repo}/${n.service_path}` : n.repo,
      group: (n.group && groupLabel.get(n.group)) || null,
      // Repo health arrives 0-100; the mark reads 0-10.
      health: h ? h.score / 10 : null,
      runtimeIn: into.get(n.id) ?? 0,
      runtimeOut: out.get(n.id) ?? 0,
      provided: n.provider_count,
      consumed: n.consumer_count,
    };
  });
}

const text = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base" });

/** The column's value; null sorts last in either order. */
function value(row: ServiceRow, key: ServiceSortKey): string | number | null {
  switch (key) {
    case "name":
      return row.name;
    case "group":
      return row.group;
    case "health":
      return row.health;
    case "in":
      return row.runtimeIn;
    case "out":
      return row.runtimeOut;
    case "contracts":
      return row.provided + row.consumed;
  }
}

/** Rows in the given order; ties and missing values fall back to the name, so the order is stable. */
export function sortServiceRows(rows: readonly ServiceRow[], sort: ServiceSort): ServiceRow[] {
  const dir: 1 | -1 = sort.order === "asc" ? 1 : -1;
  return [...rows].sort(
    (a, b) => compareValues(value(a, sort.key), value(b, sort.key), dir) || text(a.name, b.name) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
}

/** Two column values in `dir` order; a missing value goes last whichever way. */
function compareValues(va: string | number | null, vb: string | number | null, dir: 1 | -1): number {
  if (va === vb) return 0;
  if (va === null) return 1;
  if (vb === null) return -1;
  return (typeof va === "number" && typeof vb === "number" ? va - vb : text(String(va), String(vb))) * dir;
}

/** A header click: the same column flips; a new one starts A to Z for words and largest first for figures. */
export function nextSort(current: ServiceSort, key: ServiceSortKey): ServiceSort {
  if (current.key === key) return { key, order: current.order === "asc" ? "desc" : "asc" };
  return { key, order: key === "name" || key === "group" ? "asc" : "desc" };
}
