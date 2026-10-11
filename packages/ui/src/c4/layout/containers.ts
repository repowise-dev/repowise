import type { ArchNode, ArchEdge, ArchSubGroup } from "../types";
import type { ContainerAtom } from "./two-stage-layout";

export type ContainerStrategy = "curated" | "folder" | "community" | "auto";

export function buildContainers(
  nodes: ArchNode[],
  _edges: ArchEdge[],
  strategy: ContainerStrategy,
  subGroups?: ArchSubGroup[],
): ContainerAtom[] {
  // Curated grouping comes from the artifact (P3); folder/community heuristics
  // remain the fallback when curation is off or the layer has no sub-groups.
  if (strategy === "curated" && subGroups && subGroups.length > 0) {
    const curated = buildCuratedContainers(nodes, subGroups);
    if (curated.length > 0) return curated;
  }

  const resolved = resolveStrategy(strategy, nodes);
  if (resolved === "folder") {
    return buildFolderContainers(nodes);
  }
  return buildCommunityContainers(nodes);
}

function resolveStrategy(
  strategy: ContainerStrategy,
  nodes: ArchNode[],
): "folder" | "community" {
  if (strategy === "folder") return "folder";
  if (strategy === "community") return "community";
  const withPath = nodes.filter((n) => n.file_path !== null).length;
  return withPath / Math.max(nodes.length, 1) > 0.8 ? "folder" : "community";
}

function buildCuratedContainers(
  nodes: ArchNode[],
  subGroups: ArchSubGroup[],
): ContainerAtom[] {
  const visible = new Set(nodes.map((n) => n.id));

  const containers: ContainerAtom[] = [];
  for (const group of subGroups) {
    // id/name verbatim from the artifact; members limited to visible nodes
    // (persona / detail-level / user filters run upstream).
    const childNodeIds = group.node_ids.filter((id) => visible.has(id));
    if (childNodeIds.length <= 1) continue;
    containers.push({
      id: group.id,
      label: group.name,
      childNodeIds,
    });
  }

  return containers;
}

function buildFolderContainers(nodes: ArchNode[]): ContainerAtom[] {
  const groups = new Map<string, string[]>();

  for (const node of nodes) {
    if (node.file_path === null) continue;
    const dir = getDirectory(node.file_path);
    if (dir === "") continue;
    const existing = groups.get(dir);
    if (existing) {
      existing.push(node.id);
    } else {
      groups.set(dir, [node.id]);
    }
  }

  const containers: ContainerAtom[] = [];
  for (const [dir, childNodeIds] of groups) {
    if (childNodeIds.length <= 1) continue;
    const label = getDirectoryLabel(dir);
    containers.push({
      id: `dir:${dir}`,
      label,
      childNodeIds,
    });
  }

  return containers;
}

function buildCommunityContainers(nodes: ArchNode[]): ContainerAtom[] {
  const groups = new Map<number, string[]>();

  for (const node of nodes) {
    if (node.community_id === null) continue;
    const existing = groups.get(node.community_id);
    if (existing) {
      existing.push(node.id);
    } else {
      groups.set(node.community_id, [node.id]);
    }
  }

  const containers: ContainerAtom[] = [];
  for (const [communityId, childNodeIds] of groups) {
    if (childNodeIds.length <= 1) continue;
    containers.push({
      id: `community:${communityId}`,
      label: `Community ${communityId}`,
      childNodeIds,
    });
  }

  return containers;
}

function getDirectory(filePath: string): string {
  const lastSlash = filePath.lastIndexOf("/");
  if (lastSlash === -1) return "";
  return filePath.slice(0, lastSlash);
}

function getDirectoryLabel(dir: string): string {
  const parts = dir.split("/");
  return parts[parts.length - 1] ?? dir;
}

export function getStandaloneNodeIds(
  nodes: ArchNode[],
  containers: ContainerAtom[],
): string[] {
  const contained = new Set<string>();
  for (const container of containers) {
    for (const id of container.childNodeIds) {
      contained.add(id);
    }
  }
  return nodes.filter((n) => !contained.has(n.id)).map((n) => n.id);
}

/** Hard budget for visible group boxes per viewport (viewer plan P2). */
export const MAX_VISIBLE_BOXES = 12;

export interface BudgetedBoxes {
  containers: ContainerAtom[];
  standaloneIds: string[];
  /** Boxes folded into the "+N more" overflow container. */
  collapsedCount: number;
}

/** Enforce the visible-box budget: when containers + standalone cards exceed
 * *max*, the lowest-pagerank boxes collapse into one "+N more" container
 * instead of ever exceeding the budget (degradation, not truncation). */
export function enforceBoxBudget(
  containers: ContainerAtom[],
  standaloneIds: string[],
  pagerankOf: (id: string) => number,
  max: number = MAX_VISIBLE_BOXES,
): BudgetedBoxes {
  const total = containers.length + standaloneIds.length;
  if (total <= max) {
    return { containers, standaloneIds, collapsedCount: 0 };
  }

  type Box =
    | { kind: "container"; score: number; container: ContainerAtom }
    | { kind: "standalone"; score: number; id: string };

  const boxes: Box[] = [
    ...containers.map((c) => ({
      kind: "container" as const,
      score: Math.max(...c.childNodeIds.map(pagerankOf)),
      container: c,
    })),
    ...standaloneIds.map((id) => ({
      kind: "standalone" as const,
      score: pagerankOf(id),
      id,
    })),
  ].sort((a, b) => b.score - a.score);

  // Keep the strongest max-1 boxes; everything else folds into the overflow.
  const kept = boxes.slice(0, max - 1);
  const merged = boxes.slice(max - 1);

  const overflowChildren = merged.flatMap((b) =>
    b.kind === "container" ? b.container.childNodeIds : [b.id],
  );
  const overflow: ContainerAtom = {
    id: "container:__overflow",
    label: `+${merged.length} more`,
    childNodeIds: overflowChildren,
  };

  return {
    containers: [
      ...kept.filter((b) => b.kind === "container").map((b) => b.container),
      overflow,
    ],
    standaloneIds: kept.filter((b) => b.kind === "standalone").map((b) => b.id),
    collapsedCount: merged.length,
  };
}
