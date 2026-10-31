/**
 * C4 vocabulary over the zoom map: containers (deployables) and the external
 * systems the code reaches. Pure and browser-free; the canvas, the map key and
 * the detail sheet read the same helpers so they name things one way.
 */

import { techName } from "./tech-names";
import type {
  ZoomContainer,
  ZoomContainerKind,
  ZoomExternal,
  ZoomMap,
  ZoomNode,
  ZoomRelation,
} from "./types";

/**
 * Short label for a container kind (the card footer badge). Canvas labels are
 * English, like `KIND_LABEL`; the web chrome translates its own copy.
 */
export const CONTAINER_KIND_LABEL: Record<string, string> = {
  extension: "Extension",
  web_app: "Web app",
  api: "API",
  cli: "CLI",
  worker: "Worker",
  library: "Library",
};

export function containerKindLabel(kind: ZoomContainerKind): string {
  return CONTAINER_KIND_LABEL[kind] ?? techName(kind.replace(/_/g, " "));
}

/** Short label for an external system's kind (its typed boundary, else its category). */
const EXTERNAL_KIND_LABEL: Record<string, string> = {
  db: "Database",
  network: "Network",
  service: "Service",
  framework: "Framework",
  library: "Library",
  tool: "Tool",
};

export function externalKindLabel(kind: string): string {
  return EXTERNAL_KIND_LABEL[kind] ?? techName(kind);
}

/** The name an external system is shown by. */
export function externalName(e: ZoomExternal): string {
  return techName(e.display_name || e.name);
}

/**
 * The footer badge for a node, or null to keep its kind label. A container
 * label says something only where a node narrows to one deployable its parent
 * did not: a layer under the system, or a folder that narrows a spanning
 * parent. Below that every descendant would repeat the same word, so they keep
 * their kind. A node spanning several gets no badge: curated layers nearly all
 * span, so "Spans 5" on every overview card said nothing; the hover card and
 * the sheet name the containers instead.
 */
export function containerBadge(
  node: ZoomNode,
  parent: ZoomNode | undefined,
  containers: ReadonlyMap<string, ZoomContainer>,
): string | null {
  const ids = node.container_ids ?? [];
  if (ids.length === 0) return null;
  // Same set as the parent's, in any order (each side is sorted by its own files).
  const up = parent?.container_ids ?? [];
  if (up.length === ids.length && ids.every((id) => up.includes(id))) return null;
  if (ids.length > 1) return null;
  const c = containers.get(ids[0]!);
  return c ? containerKindLabel(c.kind) : null;
}

/** Ids of every node that has files in `containerId` (a spanning layer counts). */
export function containerMembers(
  nodes: readonly ZoomNode[],
  containerId: string,
): Set<string> {
  const out = new Set<string>();
  for (const n of nodes) if (n.container_ids?.includes(containerId)) out.add(n.id);
  return out;
}

/** One external system a node reaches, with the import edges from its subtree. */
export interface ExternalReach {
  external: ZoomExternal;
  edgeCount: number;
}

/** `external_relations` indexed both ways, once per map. */
export interface ExternalIndex {
  externals: ReadonlyMap<string, ZoomExternal>;
  bySource: ReadonlyMap<string, ZoomRelation[]>;
  byParent: ReadonlyMap<string, ZoomRelation[]>;
}

function push(map: Map<string, ZoomRelation[]>, key: string, r: ZoomRelation): void {
  const list = map.get(key);
  if (list) list.push(r);
  else map.set(key, [r]);
}

export function indexExternals(map: ZoomMap): ExternalIndex {
  const externals = new Map((map.externals ?? []).map((e) => [e.id, e] as const));
  const bySource = new Map<string, ZoomRelation[]>();
  const byParent = new Map<string, ZoomRelation[]>();
  for (const r of map.external_relations ?? []) {
    if (!externals.has(r.target_id)) continue;
    push(bySource, r.source_id, r);
    push(byParent, r.parent_id, r);
  }
  return { externals, bySource, byParent };
}

/**
 * The external systems `node`'s subtree reaches, most import edges first. A
 * node is the source of one link per external in its parent's bucket; the root
 * has no parent, so its reach is the sum over its children's links.
 */
export function externalsReached(index: ExternalIndex, node: ZoomNode): ExternalReach[] {
  const rels =
    node.parent_id === null ? index.byParent.get(node.id) : index.bySource.get(node.id);
  if (!rels) return [];
  const sums = new Map<string, number>();
  for (const r of rels) sums.set(r.target_id, (sums.get(r.target_id) ?? 0) + r.edge_count);
  const out: ExternalReach[] = [];
  for (const [id, edgeCount] of sums) {
    const external = index.externals.get(id);
    if (external) out.push({ external, edgeCount });
  }
  return out.sort((a, b) => b.edgeCount - a.edgeCount || (a.external.id < b.external.id ? -1 : 1));
}
