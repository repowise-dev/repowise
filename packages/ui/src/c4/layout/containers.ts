import type { ArchNode, ArchEdge } from "../types";
import type { ContainerAtom } from "./two-stage-layout";

export function buildContainers(
  nodes: ArchNode[],
  _edges: ArchEdge[],
  strategy: "folder" | "community" | "auto",
): ContainerAtom[] {
  const resolved = resolveStrategy(strategy, nodes);

  if (resolved === "folder") {
    return buildFolderContainers(nodes);
  }
  return buildCommunityContainers(nodes);
}

function resolveStrategy(
  strategy: "folder" | "community" | "auto",
  nodes: ArchNode[],
): "folder" | "community" {
  if (strategy === "folder") return "folder";
  if (strategy === "community") return "community";
  const withPath = nodes.filter((n) => n.file_path !== null).length;
  return withPath / Math.max(nodes.length, 1) > 0.8 ? "folder" : "community";
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
