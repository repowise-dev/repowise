import { useRef, useEffect, useCallback, useState } from "react";
import type Sigma from "sigma";
import type Graph from "graphology";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";
import {
  getFA2Settings,
  getLayoutDuration,
  NOVERLAP_SETTINGS,
} from "./constants";

type FA2LayoutType = import("graphology-layout-forceatlas2/worker").default;

export interface UseFA2LayoutOptions {
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  sigma: Sigma | null;
  enabled: boolean;
}

export interface UseFA2LayoutReturn {
  isRunning: boolean;
  start: () => void;
  stop: () => void;
  toggle: () => void;
}

export function useFA2Layout(
  options: UseFA2LayoutOptions,
): UseFA2LayoutReturn {
  const layoutRef = useRef<FA2LayoutType | null>(null);
  const layoutTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef(false);
  const [isRunning, setIsRunning] = useState(false);

  const convergenceIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const releaseCameraTrackingRef = useRef<(() => void) | null>(null);

  const killLayout = useCallback(() => {
    cancelledRef.current = true;
    releaseCameraTrackingRef.current?.();
    releaseCameraTrackingRef.current = null;
    if (layoutTimeoutRef.current) {
      clearTimeout(layoutTimeoutRef.current);
      layoutTimeoutRef.current = null;
    }
    if (convergenceIntervalRef.current) {
      clearInterval(convergenceIntervalRef.current);
      convergenceIntervalRef.current = null;
    }
    if (layoutRef.current) {
      layoutRef.current.kill();
      layoutRef.current = null;
    }
  }, []);

  const runLayout = useCallback(
    (graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes>) => {
      killLayout();
      cancelledRef.current = false;

      // Track whether the user touches the camera while the layout runs — if
      // they haven't, re-fit the view when it finishes so the converged graph
      // never ends up as a small cluster under a stale wide framing.
      let userMovedCamera = false;
      const markMoved = () => {
        userMovedCamera = true;
      };
      const layoutContainer = options.sigma?.getContainer();
      layoutContainer?.addEventListener("pointerdown", markMoved);
      layoutContainer?.addEventListener("wheel", markMoved, { passive: true });
      const releaseCameraTracking = () => {
        layoutContainer?.removeEventListener("pointerdown", markMoved);
        layoutContainer?.removeEventListener("wheel", markMoved);
        if (releaseCameraTrackingRef.current === releaseCameraTracking) {
          releaseCameraTrackingRef.current = null;
        }
      };
      releaseCameraTrackingRef.current = releaseCameraTracking;

      (async () => {
        const [{ default: FA2Layout }, { default: forceAtlas2 }] =
          await Promise.all([
            import("graphology-layout-forceatlas2/worker"),
            import("graphology-layout-forceatlas2"),
          ]);

        if (cancelledRef.current) return;

        const inferred = forceAtlas2.inferSettings(graph);
        const settings = { ...inferred, ...getFA2Settings(graph.order) };

        const layout = new FA2Layout(graph, { settings });
        layoutRef.current = layout;
        layout.start();
        setIsRunning(true);

        const finishLayout = async () => {
          if (convergenceIntervalRef.current) {
            clearInterval(convergenceIntervalRef.current);
            convergenceIntervalRef.current = null;
          }
          if (layoutTimeoutRef.current) {
            clearTimeout(layoutTimeoutRef.current);
            layoutTimeoutRef.current = null;
          }
          layout.stop();
          layout.kill();
          layoutRef.current = null;

          const { default: noverlap } = await import(
            "graphology-layout-noverlap"
          );
          noverlap.assign(graph, NOVERLAP_SETTINGS);
          options.sigma?.refresh();
          // Re-frame the converged layout — but never yank a camera the user
          // has already panned or zoomed.
          if (!userMovedCamera) {
            options.sigma?.getCamera().animatedReset({ duration: 300 });
          }
          releaseCameraTracking();
          setIsRunning(false);
        };

        // Convergence detection: sample 100 random nodes every 500ms
        const sampleSize = Math.min(100, graph.order);
        const allNodeIds = graph.nodes();
        const nodeIds: string[] = [];
        if (sampleSize >= allNodeIds.length) {
          nodeIds.push(...allNodeIds);
        } else {
          const stride = allNodeIds.length / sampleSize;
          for (let i = 0; i < sampleSize; i++) {
            nodeIds.push(
              allNodeIds[
                Math.floor((i + Math.random()) * stride) % allNodeIds.length
              ]!,
            );
          }
        }
        let prevPositions = nodeIds.map((id) => {
          const a = graph.getNodeAttributes(id);
          return { x: a.x, y: a.y };
        });

        convergenceIntervalRef.current = setInterval(() => {
          if (cancelledRef.current) return;
          const currentPositions = nodeIds.map((id) => {
            const a = graph.getNodeAttributes(id);
            return { x: a.x, y: a.y };
          });
          let totalDelta = 0;
          for (let i = 0; i < nodeIds.length; i++) {
            const dx = currentPositions[i]!.x - prevPositions[i]!.x;
            const dy = currentPositions[i]!.y - prevPositions[i]!.y;
            totalDelta += Math.sqrt(dx * dx + dy * dy);
          }
          const avgDelta = totalDelta / nodeIds.length;
          prevPositions = currentPositions;
          if (avgDelta < 0.5) {
            finishLayout();
          }
        }, 500);

        // Hard cap timeout as fallback
        layoutTimeoutRef.current = setTimeout(() => {
          finishLayout();
        }, getLayoutDuration(graph.order));
      })();
    },
    [killLayout, options.sigma],
  );

  // Start layout when enabled and graph is ready. Graphs the adapter already
  // settled synchronously (see settleGraph) skip the auto-run — their first
  // frame IS the final layout; the manual toggle below can still start FA2.
  useEffect(() => {
    if (
      options.enabled &&
      options.graph &&
      options.graph.order > 0 &&
      !options.graph.getAttribute("presettled")
    ) {
      runLayout(options.graph);
    } else {
      killLayout();
      setIsRunning(false);
    }
    return () => {
      killLayout();
    };
  }, [options.enabled, options.graph, runLayout, killLayout]);

  const start = useCallback(() => {
    if (options.graph && options.graph.order > 0) {
      runLayout(options.graph);
    }
  }, [options.graph, runLayout]);

  const stop = useCallback(() => {
    killLayout();
    if (options.graph) {
      const g = options.graph;
      const s = options.sigma;
      import("graphology-layout-noverlap").then(({ default: noverlap }) => {
        noverlap.assign(g, NOVERLAP_SETTINGS);
        s?.refresh();
      });
    }
    setIsRunning(false);
  }, [killLayout, options.graph, options.sigma]);

  const toggle = useCallback(() => {
    if (isRunning) {
      stop();
    } else {
      start();
    }
  }, [isRunning, start, stop]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      killLayout();
    };
  }, [killLayout]);

  return { isRunning, start, stop, toggle };
}
