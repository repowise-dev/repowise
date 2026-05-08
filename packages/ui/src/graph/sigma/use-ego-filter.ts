import { useMemo } from "react";
import type Graph from "graphology";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";

export interface UseEgoFilterOptions {
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  selectedNodeId: string | null;
  depth: number;
}

export interface UseEgoFilterReturn {
  egoNodes: Set<string>;
  hiddenNodes: Set<string>;
  isActive: boolean;
  visibleCount: number;
}

export function useEgoFilter(options: UseEgoFilterOptions): UseEgoFilterReturn {
  const { graph, selectedNodeId, depth } = options;

  return useMemo(() => {
    if (
      depth === 0 ||
      !selectedNodeId ||
      !graph ||
      !graph.hasNode(selectedNodeId)
    ) {
      return {
        egoNodes: new Set<string>(),
        hiddenNodes: new Set<string>(),
        isActive: false,
        visibleCount: 0,
      };
    }

    const visited = new Set<string>([selectedNodeId]);
    let frontier = [selectedNodeId];
    for (let d = 0; d < depth; d++) {
      const nextFrontier: string[] = [];
      for (const node of frontier) {
        for (const neighbor of graph.neighbors(node)) {
          if (!visited.has(neighbor)) {
            visited.add(neighbor);
            nextFrontier.push(neighbor);
          }
        }
      }
      frontier = nextFrontier;
    }

    const hiddenNodes = new Set<string>();
    graph.forEachNode((nodeId) => {
      if (!visited.has(nodeId)) {
        hiddenNodes.add(nodeId);
      }
    });

    return {
      egoNodes: visited,
      hiddenNodes,
      isActive: true,
      visibleCount: visited.size,
    };
  }, [graph, selectedNodeId, depth]);
}
