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

export interface UseElkSigmaLayoutOptions {
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  sigma: Sigma | null;
  enabled: boolean;
  fileNodes?: GraphNodeResponse[] | undefined;
  fileEdges?: GraphEdgeResponse[] | undefined;
  moduleNodes?: ModuleNodeResponse[] | undefined;
  moduleEdges?: ModuleEdgeResponse[] | undefined;
  viewMode: ViewMode;
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

  const compute = useCallback(() => {
    const {
      graph,
      sigma,
      fileNodes,
      fileEdges,
      moduleNodes,
      moduleEdges,
      viewMode,
    } = options;
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
  }, [options]);

  useEffect(() => {
    if (options.enabled && options.graph && options.graph.order > 0) {
      compute();
    }
  }, [options.enabled, options.graph, compute]);

  return { isComputing, recompute: compute };
}
