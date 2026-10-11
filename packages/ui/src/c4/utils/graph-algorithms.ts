import type { ArchEdge } from "../types";

export function findShortestPath(
  edges: ArchEdge[],
  sourceId: string,
  targetId: string,
): string[] | null {
  if (sourceId === targetId) return [sourceId];

  const adj = new Map<string, Set<string>>();
  for (const edge of edges) {
    if (!adj.has(edge.source)) adj.set(edge.source, new Set());
    if (!adj.has(edge.target)) adj.set(edge.target, new Set());
    adj.get(edge.source)!.add(edge.target);
    adj.get(edge.target)!.add(edge.source);
  }

  if (!adj.has(sourceId) || !adj.has(targetId)) return null;

  const visited = new Set<string>([sourceId]);
  const parent = new Map<string, string>();
  const queue: string[] = [sourceId];

  while (queue.length > 0) {
    const current = queue.shift()!;
    if (current === targetId) {
      const path: string[] = [];
      let node: string | undefined = targetId;
      while (node !== undefined) {
        path.unshift(node);
        node = parent.get(node);
      }
      return path;
    }
    const neighbors = adj.get(current);
    if (neighbors) {
      for (const neighbor of neighbors) {
        if (!visited.has(neighbor)) {
          visited.add(neighbor);
          parent.set(neighbor, current);
          queue.push(neighbor);
        }
      }
    }
  }

  return null;
}
