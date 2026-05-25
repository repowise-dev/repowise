import type { ArchEdge } from "../types";

export interface AggregatedEdge {
  id: string;
  source: string;
  target: string;
  count: number;
  dominantType: string;
  types: string[];
}

export function aggregateEdges(
  edges: ArchEdge[],
  nodeToBox: Map<string, string>,
): AggregatedEdge[] {
  const groups = new Map<string, { source: string; target: string; types: string[] }>();

  for (const edge of edges) {
    const sourceBox = nodeToBox.get(edge.source);
    const targetBox = nodeToBox.get(edge.target);
    if (sourceBox === undefined || targetBox === undefined) continue;
    if (sourceBox === targetBox) continue;

    const key = `${sourceBox}→${targetBox}`;
    const existing = groups.get(key);
    if (existing) {
      existing.types.push(edge.edge_type);
    } else {
      groups.set(key, { source: sourceBox, target: targetBox, types: [edge.edge_type] });
    }
  }

  const result: AggregatedEdge[] = [];
  for (const [key, group] of groups) {
    const typeCounts = new Map<string, number>();
    for (const t of group.types) {
      typeCounts.set(t, (typeCounts.get(t) ?? 0) + 1);
    }

    let dominantType = "";
    let maxCount = 0;
    for (const [t, count] of typeCounts) {
      if (count > maxCount) {
        maxCount = count;
        dominantType = t;
      }
    }

    const uniqueTypes = [...new Set(group.types)];
    result.push({
      id: `agg:${key}`,
      source: group.source,
      target: group.target,
      count: group.types.length,
      dominantType,
      types: uniqueTypes,
    });
  }

  result.sort((a, b) => b.count - a.count);
  return result;
}
