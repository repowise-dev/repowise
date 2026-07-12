"use client";

import { useRef, useEffect, useCallback, useState } from "react";
import type Sigma from "sigma";
import type Graph from "graphology";
import type {
  GraphNode as GraphNodeResponse,
  GraphLink as GraphEdgeResponse,
  ModuleNode as ModuleNodeResponse,
  ModuleEdge as ModuleEdgeResponse,
} from "@repowise-dev/types/graph";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";
import type { ViewMode } from "../graph-toolbar";
import {
  computeElkFilePositions,
  computeElkModulePositions,
} from "../elk-layout";

// ELK runs on the main thread (elk.bundled.js, no worker), so a large graph
// would freeze the tab mid-layout. 500 nodes keeps the compute comfortably
// interactive; raising this ceiling means moving ELK into a web worker first.
export const ELK_MAX_NODES = 500;

export function elkSkipReason(order: number): string {
  return `Hierarchical layout is limited to ${ELK_MAX_NODES} nodes — this view has ${order.toLocaleString()}. Switch to the Modules scope or narrow the view to use it.`;
}

export interface UseElkSigmaLayoutOptions {
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  sigma: Sigma | null;
  enabled: boolean;
  fileNodes?: GraphNodeResponse[] | undefined;
  fileEdges?: GraphEdgeResponse[] | undefined;
  moduleNodes?: ModuleNodeResponse[] | undefined;
  moduleEdges?: ModuleEdgeResponse[] | undefined;
  viewMode: ViewMode;
  onSkipped?: ((reason: string) => void) | undefined;
}

export interface UseElkSigmaLayoutReturn {
  isComputing: boolean;
  recompute: () => void;
}

export function useElkSigmaLayout(
  options: UseElkSigmaLayoutOptions,
): UseElkSigmaLayoutReturn {
  const [isComputing, setIsComputing] = useState(false);
  const computeIdRef = useRef(0);

  const {
    graph,
    sigma,
    enabled,
    fileNodes,
    fileEdges,
    moduleNodes,
    moduleEdges,
    viewMode,
  } = options;

  const compute = useCallback(() => {
    if (!graph || graph.order === 0 || !sigma) return;

    const computeId = ++computeIdRef.current;
    setIsComputing(true);

    const run = async () => {
      try {
        if (viewMode === "module" && moduleNodes && moduleEdges) {
          const positions = await computeElkModulePositions(
            moduleNodes,
            moduleEdges,
          );
          if (computeIdRef.current !== computeId) return;
          graph.forEachNode((nodeId) => {
            const pos = positions.get(nodeId);
            if (pos) {
              graph.setNodeAttribute(nodeId, "x", pos.x);
              graph.setNodeAttribute(nodeId, "y", pos.y);
            }
          });
        } else if (fileNodes && fileEdges) {
          const result = await computeElkFilePositions(fileNodes, fileEdges);
          if (computeIdRef.current !== computeId) return;
          graph.forEachNode((nodeId) => {
            const pos = result.positions.get(nodeId);
            if (pos) {
              graph.setNodeAttribute(nodeId, "x", pos.x);
              graph.setNodeAttribute(nodeId, "y", pos.y);
            }
          });
        }
        sigma.refresh();
        sigma.getCamera().animatedReset({ duration: 500 });
      } finally {
        if (computeIdRef.current === computeId) {
          setIsComputing(false);
        }
      }
    };

    void run();
  }, [graph, sigma, fileNodes, fileEdges, moduleNodes, moduleEdges, viewMode]);

  useEffect(() => {
    if (enabled && graph && graph.order > 0) {
      if (graph.order > ELK_MAX_NODES) {
        options.onSkipped?.(elkSkipReason(graph.order));
        return;
      }
      compute();
    }
  }, [enabled, graph, compute, options.onSkipped]);

  return { isComputing, recompute: compute };
}
