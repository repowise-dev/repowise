/**
 * Edge families: the four groups the map filters by (Runtime, Data, Build,
 * History). Which kind belongs to which family lives on the kind registry
 * (`SYSTEM_EDGE_KINDS[kind].family`), so a new transport declares its family
 * in the one row it already adds. Pure, no React.
 */

import { edgeKindStyle, type EdgeFamily } from "./edge-kinds";

export type { EdgeFamily };

/** Display order of the family chips. */
export const EDGE_FAMILY_ORDER: readonly EdgeFamily[] = ["runtime", "data", "build", "history"];

/** Drawn on first load: calls, events and shared data. Build and History are opt-in. */
export const DEFAULT_EDGE_FAMILIES: ReadonlySet<EdgeFamily> = new Set<EdgeFamily>(["runtime", "data"]);

/**
 * The families that count as "linked" when the map ranks services and fills
 * the "Not linked at runtime" tray. Fixed on purpose, independent of the
 * filter, so toggling a family never moves a service in or out of the tray
 * and never reorders what the draw budget keeps.
 */
export const LINKED_FAMILIES: ReadonlySet<EdgeFamily> = DEFAULT_EDGE_FAMILIES;

/** The family of an edge kind from the registry; an unknown kind is History, as in core. */
export function edgeFamily(kind: string): EdgeFamily {
  return edgeKindStyle(kind).family;
}

const FAMILIES: ReadonlySet<string> = new Set(EDGE_FAMILY_ORDER);

/**
 * An edge's family: the `family` the server sent when it is one we know,
 * else the kind registry's (payloads written before the field existed).
 */
export function edgeFamilyOf(edge: { kind: string; family?: string | null | undefined }): EdgeFamily {
  const sent = edge.family;
  return sent && FAMILIES.has(sent) ? (sent as EdgeFamily) : edgeFamily(edge.kind);
}

export interface FamilyCount {
  /** System edges of the family. */
  edges: number;
  /** Underlying links (sum of edge weights). */
  links: number;
}

/** Edge and link counts per family, for the chips and the "hidden" caption. Only families present appear. */
export function familyCounts(
  edges: Iterable<{ kind: string; family?: string | null | undefined; weight: number }>,
): Partial<Record<EdgeFamily, FamilyCount>> {
  const out: Partial<Record<EdgeFamily, FamilyCount>> = {};
  for (const e of edges) {
    const family = edgeFamilyOf(e);
    const row = (out[family] ??= { edges: 0, links: 0 });
    row.edges += 1;
    row.links += linkWeight(e);
  }
  return out;
}

/** Links an edge stands for. An edge exists, so it is never fewer than one. */
export function linkWeight(edge: { weight: number }): number {
  return edge.weight > 1 ? edge.weight : 1;
}
