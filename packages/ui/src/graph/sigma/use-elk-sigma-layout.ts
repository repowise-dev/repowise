"use client";

import { useRef, useEffect, useCallback, useState } from "react";
import type Sigma from "sigma";
import type Graph from "graphology";
import type {
  GraphNode as GraphNodeResponse,
  GraphLink as GraphEdgeResponse,
} from "@repowise-dev/types/graph";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";
import { computeElkFilePositions } from "../elk-layout";

// ELK runs on the main thread (elk.bundled.js, no worker), so a large graph
// would freeze the tab mid-layout. 500 nodes keeps the compute comfortably
// interactive; raising this ceiling means moving ELK into a web worker first.
export const ELK_MAX_NODES = 500;

/**
 * States the bound and stops there, deliberately.
 *
 * This used to end "Switch to the Modules scope or narrow the view to use it."
 * Every one of those was a dead end: the Modules scope is gone, and the module
 * filter, the community filter and search all *dim* rather than remove, so
 * `graph.order` — the number this cap is measured against — does not move when
 * you use them. Advice that cannot work is worse than no advice.
 */
export function elkSkipReason(order: number): string {
  return `Hierarchical layout needs ${ELK_MAX_NODES} nodes or fewer — this view has ${order.toLocaleString()}.`;
}

/**
 * Write computed positions onto the graph in ONE pass. Graphology emits an
 * event per `setNodeAttribute`, so the obvious `forEachNode` + two setters
 * fired 2 events per node — 3,000 for a 500-node ELK run's worth of listeners
 * to walk. `updateEachNodeAttributes` with an explicit attribute hint emits a
 * single `eachNodeAttributesUpdated` instead.
 */
function applyPositions(
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes>,
  positions: Map<string, { x: number; y: number }>,
): void {
  graph.updateEachNodeAttributes(
    (nodeId, attrs) => {
      const pos = positions.get(nodeId);
      return pos ? { ...attrs, x: pos.x, y: pos.y } : attrs;
    },
    { attributes: ["x", "y"] },
  );
}

export interface UseElkSigmaLayoutOptions {
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  sigma: Sigma | null;
  enabled: boolean;
  fileNodes?: GraphNodeResponse[] | undefined;
  fileEdges?: GraphEdgeResponse[] | undefined;
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
  } = options;

  const compute = useCallback(() => {
    if (!graph || graph.order === 0 || !sigma) return;

    const computeId = ++computeIdRef.current;
    setIsComputing(true);

    const run = async () => {
      try {
        if (fileNodes && fileEdges) {
          const result = await computeElkFilePositions(fileNodes, fileEdges);
          if (computeIdRef.current !== computeId) return;
          applyPositions(graph, result.positions);
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
  }, [graph, sigma, fileNodes, fileEdges]);

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
