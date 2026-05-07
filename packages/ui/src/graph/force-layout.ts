import {
  forceSimulation,
  forceManyBody,
  forceLink,
  forceCollide,
  forceCenter,
} from "d3-force";
import type { Node, Edge } from "@xyflow/react";
import type {
  GraphNode as GraphNodeResponse,
  GraphLink as GraphEdgeResponse,
} from "@repowise-dev/types/graph";
import type { FileNodeData, DependencyEdgeData } from "./elk-layout";

export const FORCE_COMMUNITY_PALETTE = [
  "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
  "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
];

export interface ForceLayoutOptions {
  communityMap?: Map<string, number>;
}

interface ForceNode {
  id: string;
  index?: number;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
  apiNode: GraphNodeResponse;
}

function deduplicateEdges(
  edges: GraphEdgeResponse[],
): { source: string; target: string; importedNames: string[]; edgeCount: number }[] {
  const map = new Map<
    string,
    { source: string; target: string; importedNames: string[]; edgeCount: number }
  >();
  for (const e of edges) {
    const key = `${e.source}→${e.target}`;
    const existing = map.get(key);
    if (existing) {
      existing.importedNames.push(...e.imported_names);
      existing.edgeCount++;
    } else {
      map.set(key, {
        source: e.source,
        target: e.target,
        importedNames: [...e.imported_names],
        edgeCount: 1,
      });
    }
  }
  return Array.from(map.values());
}

function communityClusteringForce(communityMap: Map<string, number>, strength: number) {
  let nodes: ForceNode[] = [];

  function force(alpha: number) {
    const centroids = new Map<number, { x: number; y: number; count: number }>();
    for (const node of nodes) {
      const cId = communityMap.get(node.id) ?? 0;
      const entry = centroids.get(cId) ?? { x: 0, y: 0, count: 0 };
      entry.x += node.x ?? 0;
      entry.y += node.y ?? 0;
      entry.count++;
      centroids.set(cId, entry);
    }
    for (const c of centroids.values()) {
      c.x /= c.count;
      c.y /= c.count;
    }
    for (const node of nodes) {
      const cId = communityMap.get(node.id) ?? 0;
      const c = centroids.get(cId);
      if (c) {
        node.vx = (node.vx ?? 0) + (c.x - (node.x ?? 0)) * strength * alpha;
        node.vy = (node.vy ?? 0) + (c.y - (node.y ?? 0)) * strength * alpha;
      }
    }
  }

  force.initialize = (n: ForceNode[]) => {
    nodes = n;
  };
  return force;
}

export function computeForceLayout(
  apiNodes: GraphNodeResponse[],
  apiEdges: GraphEdgeResponse[],
  options?: ForceLayoutOptions,
): { nodes: Node[]; edges: Edge[] } {
  if (apiNodes.length === 0) return { nodes: [], edges: [] };

  const nodeSet = new Set(apiNodes.map((n) => n.node_id));
  const validEdges = apiEdges.filter((e) => nodeSet.has(e.source) && nodeSet.has(e.target));
  const dedupedEdges = deduplicateEdges(validEdges);

  const forceNodes: ForceNode[] = apiNodes.map((n) => ({
    id: n.node_id,
    apiNode: n,
  }));

  const forceLinks = dedupedEdges
    .filter((e) => nodeSet.has(e.source) && nodeSet.has(e.target))
    .map((e) => ({ source: e.source, target: e.target }));

  const communityMap =
    options?.communityMap ??
    new Map(apiNodes.map((n) => [n.node_id, n.community_id]));

  /* eslint-disable @typescript-eslint/no-explicit-any */
  const sim = forceSimulation(forceNodes as any[])
    .force("charge", forceManyBody().strength(-60))
    .force(
      "link",
      forceLink(forceLinks as any[])
        .id((d: any) => d.id)
        .distance(120),
    )
    .force("collide", forceCollide(20))
    .force("center", forceCenter(0, 0))
    .force("community", communityClusteringForce(communityMap, 0.15) as any)
    .stop();
  /* eslint-enable @typescript-eslint/no-explicit-any */

  for (let i = 0; i < 200; i++) sim.tick();

  const rfNodes: Node[] = forceNodes.map((fn) => ({
    id: fn.id,
    type: "fileNode",
    position: { x: fn.x ?? 0, y: fn.y ?? 0 },
    data: {
      label: fn.id.split("/").pop() ?? fn.id,
      fullPath: fn.apiNode.node_id,
      language: fn.apiNode.language,
      symbolCount: fn.apiNode.symbol_count,
      pagerank: fn.apiNode.pagerank,
      betweenness: fn.apiNode.betweenness,
      communityId: fn.apiNode.community_id,
      isTest: fn.apiNode.is_test,
      isEntryPoint: fn.apiNode.is_entry_point,
      hasDoc: fn.apiNode.has_doc,
    } satisfies FileNodeData,
  }));

  const rfEdges: Edge[] = dedupedEdges.map((e) => ({
    id: `${e.source}→${e.target}`,
    source: e.source,
    target: e.target,
    type: "dependency",
    data: {
      importedNames: e.importedNames,
      edgeCount: e.edgeCount,
    } satisfies DependencyEdgeData,
  }));

  return { nodes: rfNodes, edges: rfEdges };
}
