"use client";

/**
 * Computes the React Flow node/edge arrays for the Live System Map: applies the
 * view filters, runs the async ELK layout, then joins health + overlay onto
 * each element. Mirrors the C4 view's `use-c4-layout` pattern (async layout,
 * loading flag, cancel-on-unmount). Pure inputs in, render-ready arrays out.
 */

import { useEffect, useMemo, useState } from "react";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { SystemGraph } from "@repowise-dev/types";
import { applyView, computeSystemMapPositions, type SystemMapView } from "./layout";
import {
  resolveEdgeOverlay,
  resolveNodeOverlay,
  type RepoHealth,
  type SystemMapEdgeData,
  type SystemMapNodeData,
  type SystemMapOverlay,
} from "./types";

export interface UseSystemMapLayoutArgs {
  graph: SystemGraph | null;
  view: SystemMapView;
  /** Repo health by repo alias, joined onto service nodes (optional). */
  healthByRepo?: ReadonlyMap<string, RepoHealth>;
  overlay?: SystemMapOverlay;
}

export interface SystemMapLayout {
  nodes: Node<SystemMapNodeData>[];
  edges: Edge<SystemMapEdgeData>[];
  loading: boolean;
}

export function useSystemMapLayout({
  graph,
  view,
  healthByRepo,
  overlay,
}: UseSystemMapLayoutArgs): SystemMapLayout {
  const viewGraph = useMemo(() => (graph ? applyView(graph, view) : null), [graph, view]);
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }> | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    if (!viewGraph || viewGraph.nodes.length === 0) {
      setPositions(new Map());
      setLoading(false);
      return;
    }
    setLoading(true);
    computeSystemMapPositions(viewGraph).then((pos) => {
      if (!cancelled) {
        setPositions(pos);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [viewGraph]);

  const nodes = useMemo<Node<SystemMapNodeData>[]>(() => {
    if (!viewGraph || !positions) return [];
    return viewGraph.nodes.map((node) => {
      const pos = positions.get(node.id);
      return {
        id: node.id,
        type: "systemService",
        position: { x: pos?.x ?? 0, y: pos?.y ?? 0 },
        data: {
          node,
          health: healthByRepo?.get(node.repo) ?? null,
          overlay: resolveNodeOverlay(overlay, node.id),
        },
      };
    });
  }, [viewGraph, positions, healthByRepo, overlay]);

  const edges = useMemo<Edge<SystemMapEdgeData>[]>(() => {
    if (!viewGraph) return [];
    return viewGraph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "systemEdge",
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: "var(--color-diagram-edge)" },
      data: { edge, overlay: resolveEdgeOverlay(overlay, edge.id) },
    }));
  }, [viewGraph, overlay]);

  return { nodes, edges, loading };
}
