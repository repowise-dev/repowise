/**
 * What the C4 diagram draws at rest. The raw container view of a monorepo
 * carries every package dependency (this repo's L2: 74 externals, 129
 * relations), which buries the few links that explain the system. By default
 * the diagram keeps:
 *
 * - relations between the system's own boxes, except loose ones;
 * - relations to an external *system* (a service or a database). A library
 *   or framework is a build dependency, not a system the code talks to.
 *
 * Externals left with no drawn relation leave with them. "Show all" draws the
 * view as served.
 */

import type { ZoomContainer } from "@repowise-dev/ui/zoom";
import type { C4Container, C4ExternalSystem, C4L2, C4L3, C4Relation } from "@repowise-dev/ui/c4";

type LinkedView = C4L2 | C4L3;

function isExternalSystem(e: C4ExternalSystem): boolean {
  return e.category === "service" || e.io_kind === "db";
}

function keep(r: C4Relation, externals: ReadonlyMap<string, C4ExternalSystem>): boolean {
  const ext = externals.get(r.target_id);
  if (ext) return isExternalSystem(ext);
  return r.coupling !== "loose";
}

export function strongLinks<V extends LinkedView>(view: V): { view: V; hidden: number } {
  const externals = new Map(view.external_systems.map((e) => [e.id, e] as const));
  const relations = view.relations.filter((r) => keep(r, externals));
  const reached = new Set(relations.map((r) => r.target_id));
  return {
    view: {
      ...view,
      relations,
      external_systems: view.external_systems.filter((e) => reached.has(e.id)),
    },
    hidden: view.relations.length - relations.length,
  };
}

/**
 * The L2 containers with the zoom map's kind and technologies joined in (same
 * `pkg:<path>` ids), so the diagram can tell a library from a container that
 * runs on its own.
 */
export function withContainerKinds(view: C4L2, known: readonly ZoomContainer[] | undefined): C4L2 {
  if (!known?.length) return view;
  const byId = new Map(known.map((c) => [c.id, c] as const));
  const containers = view.containers.map((c): C4Container => {
    const z = byId.get(c.id);
    return z ? { ...c, kind: z.kind, technologies: z.technologies } : c;
  });
  return { ...view, containers };
}
