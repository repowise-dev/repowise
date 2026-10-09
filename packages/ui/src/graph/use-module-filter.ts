import { useMemo } from "react";
import type { GraphNode } from "@repowise-dev/types/graph";

/** Bucket every path outside the repo's own tree, however it is spelled. */
const EXTERNAL_GROUP = "external";
/** Files that live at the repo root and have no directory to belong to. */
const ROOT_GROUP = "(repo root)";

/**
 * The module a file path belongs to, as a *path prefix* rather than a
 * top-level directory.
 *
 * Top-level directories do not partition this kind of repo: on repowise itself
 * `packages/` holds 92% of the files, so a top-level filter would dim 8% of the
 * canvas and read as broken. Two segments do partition it — the largest group
 * becomes 38% and the top five cover 84%.
 *
 * The second segment is only taken when it is a directory (path depth 3+).
 * Otherwise `tests/conftest.py` would invent a one-file module called
 * "tests/conftest.py" sitting beside the real `tests/unit`.
 */
export function moduleGroupFor(nodeId: string): string {
  if (nodeId.startsWith("external:") || nodeId.startsWith("framework:")) {
    return EXTERNAL_GROUP;
  }
  const parts = nodeId.split("/");
  if (parts.length === 1) return ROOT_GROUP;
  if (parts.length === 2) return parts[0]!;
  return `${parts[0]}/${parts[1]}`;
}

export interface ModuleGroup {
  id: string;
  fileCount: number;
}

/**
 * Filter the file graph by module: a path prefix, everything outside it
 * **removed** from the graph that gets built.
 *
 * It used to dim instead. Dimming 1,500 nodes means reading the answer through
 * fog while still paying to lay out, colour and draw every node you asked to
 * hide — the worst of both. Removing them is the honest reading of "filter" and
 * it is also strictly cheaper: the graphology build, the layout and the render
 * all shrink to what you selected.
 *
 * The group list is derived from the **unfiltered** nodes, so choosing a module
 * never collapses the menu you chose it from.
 *
 * This replaces a whole separate "Modules" scope, which drew one circle per
 * top-level directory: a 9-item list where one item held 69% of the files,
 * with its own endpoint, breadcrumb trail, drill-down state and
 * expand-on-double-click. A skewed list is a bad canvas and a fine filter.
 */
export function useModuleFilter(
  nodes: readonly GraphNode[] | undefined,
  activeModule: string | null,
) {
  const moduleGroups = useMemo<ModuleGroup[]>(() => {
    if (!nodes) return [];
    const counts = new Map<string, number>();
    for (const node of nodes) {
      const group = moduleGroupFor(node.node_id);
      counts.set(group, (counts.get(group) ?? 0) + 1);
    }
    return [...counts]
      .map(([id, fileCount]) => ({ id, fileCount }))
      .sort((a, b) => b.fileCount - a.fileCount || a.id.localeCompare(b.id));
  }, [nodes]);

  /** How many nodes the active module actually matches — the control reports
   *  this, so it can never claim a filter that selected nothing. */
  const activeModuleCount = useMemo(() => {
    if (!activeModule) return null;
    return moduleGroups.find((g) => g.id === activeModule)?.fileCount ?? 0;
  }, [activeModule, moduleGroups]);

  return { moduleGroups, activeModuleCount };
}

/**
 * Narrow a file-graph payload to one module, dropping any link that lost an
 * endpoint. Pure, so the caller can memo it beside the rest of its graph data.
 * Returns the input unchanged when nothing is selected.
 */
export function filterGraphToModule<
  N extends { node_id: string },
  L extends { source: string; target: string },
>(
  data: { nodes: N[]; links: L[] },
  activeModule: string | null,
): { nodes: N[]; links: L[] } {
  if (!activeModule) return data;
  // No escape hatch when this matches nothing: a stale `?module=` from an old
  // link would otherwise render the whole graph under a control claiming one
  // module. The empty canvas plus the control's own count is the honest pair.
  const nodes = data.nodes.filter((n) => moduleGroupFor(n.node_id) === activeModule);
  const kept = new Set(nodes.map((n) => n.node_id));
  const links = data.links.filter((l) => kept.has(l.source) && kept.has(l.target));
  return { nodes, links };
}
