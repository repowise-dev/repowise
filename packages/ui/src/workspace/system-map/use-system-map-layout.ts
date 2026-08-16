"use client";

/**
 * Computes the React Flow node/edge arrays for the Live System Map: applies the
 * view filters, runs the async ELK layout, then joins health + overlay onto
 * each element. Mirrors the C4 view's `use-c4-layout` pattern (async layout,
 * loading flag, cancel-on-unmount). Pure inputs in, render-ready arrays out.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { SystemGraph } from "@repowise-dev/types";
import {
  applyCollapse,
  applyView,
  computeSystemMapPositions,
  layoutSignature,
  type SystemMapView,
} from "./layout";
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
  /**
   * The graph actually on screen (collapse + filters applied). The inspector
   * must resolve selections against this, not the raw graph: in repo view the
   * ids it can be handed only exist here.
   */
  viewGraph: SystemGraph | null;
  /** True when the node count forced the grid fallback instead of ELK. */
  simplified: boolean;
}

export function useSystemMapLayout({
  graph,
  view,
  healthByRepo,
  overlay,
}: UseSystemMapLayoutArgs): SystemMapLayout {
  const viewGraph = useMemo(() => (graph ? applyView(graph, view) : null), [graph, view]);

  // Laid out from the collapse state only. Toggling an edge-kind filter leaves
  // this identical, so the effect below sees the same signature and never
  // re-runs ELK — the map holds still and the ~23ms main-thread cost is not paid.
  const layoutGraph = useMemo(
    () => (graph ? applyCollapse(graph, view.collapsed) : null),
    [graph, view.collapsed],
  );
  const signature = useMemo(
    () => (layoutGraph ? layoutSignature(layoutGraph) : null),
    [layoutGraph],
  );

  const [layout, setLayout] = useState<{
    positions: Map<string, { x: number; y: number }>;
    simplified: boolean;
  } | null>(null);
  const [loading, setLoading] = useState(true);

  // Positions keyed by layout signature, so collapsing and un-collapsing is free
  // after the first pass in each direction. Dropped whenever a new graph arrives:
  // one graph has only the two collapse shapes, so holding entries for superseded
  // graphs would grow the map for refetches it can never serve again.
  const cache = useRef(
    new Map<string, { positions: Map<string, { x: number; y: number }>; simplified: boolean }>(),
  );
  const cachedGraph = useRef(graph);
  if (cachedGraph.current !== graph) {
    cachedGraph.current = graph;
    cache.current.clear();
  }

  useEffect(() => {
    let cancelled = false;
    if (!layoutGraph || !signature || layoutGraph.nodes.length === 0) {
      setLayout({ positions: new Map(), simplified: false });
      setLoading(false);
      return;
    }
    const cached = cache.current.get(signature);
    if (cached) {
      setLayout(cached);
      setLoading(false);
      return;
    }
    setLoading(true);
    computeSystemMapPositions(layoutGraph).then((result) => {
      cache.current.set(signature, result);
      if (!cancelled) {
        setLayout(result);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [layoutGraph, signature]);

  const positions = layout?.positions ?? null;

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

  return { nodes, edges, loading, viewGraph, simplified: layout?.simplified ?? false };
}
