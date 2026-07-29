import { useCallback, useMemo, useState } from "react";
import type Graph from "graphology";
import type { SigmaEdgeAttributes, SigmaNodeAttributes } from "./sigma/types";

type SigmaGraph = Graph<SigmaNodeAttributes, SigmaEdgeAttributes>;

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
 * Filter the file graph by module, by dimming everything outside the selected
 * module rather than removing it — the surrounding structure is the context
 * that makes "this module" mean anything, and it is the same grammar the
 * community filter, the ego filter and search already use.
 *
 * `activeModule === null` means no filter.
 *
 * This replaces a whole separate "Modules" scope, which drew one circle per
 * top-level directory: a 9-item list where one item held 69% of the files,
 * with its own endpoint, breadcrumb trail, drill-down state and
 * expand-on-double-click. A skewed list is a bad canvas and a fine filter.
 */
export function useModuleFilter(sigmaGraph: SigmaGraph | null) {
  const [activeModule, setActiveModule] = useState<string | null>(null);

  const moduleGroups = useMemo<ModuleGroup[]>(() => {
    if (!sigmaGraph) return [];
    const counts = new Map<string, number>();
    sigmaGraph.forEachNode((_nodeId, attrs) => {
      if (attrs.nodeType !== "file") return;
      const group = moduleGroupFor(attrs.fullPath);
      counts.set(group, (counts.get(group) ?? 0) + 1);
    });
    return [...counts]
      .map(([id, fileCount]) => ({ id, fileCount }))
      .sort((a, b) => b.fileCount - a.fileCount || a.id.localeCompare(b.id));
  }, [sigmaGraph]);

  const moduleDimmedNodes = useMemo(() => {
    if (!activeModule || !sigmaGraph) return null;
    const dimmed = new Set<string>();
    sigmaGraph.forEachNode((nodeId, attrs) => {
      if (attrs.nodeType !== "file") return;
      if (moduleGroupFor(attrs.fullPath) !== activeModule) dimmed.add(nodeId);
    });
    return dimmed.size > 0 ? dimmed : null;
  }, [activeModule, sigmaGraph]);

  /** How many nodes the active module actually matches — the control reports
   *  this, so it can never claim a filter that selected nothing. */
  const activeModuleCount = useMemo(() => {
    if (!activeModule) return null;
    return moduleGroups.find((g) => g.id === activeModule)?.fileCount ?? 0;
  }, [activeModule, moduleGroups]);

  const handleModuleChange = useCallback((next: string | null) => {
    setActiveModule(next);
  }, []);

  return {
    activeModule,
    activeModuleCount,
    moduleGroups,
    moduleDimmedNodes,
    handleModuleChange,
  };
}
