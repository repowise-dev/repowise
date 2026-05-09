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
  const [isRunning, setIsRunning] = useState(false);

  const killLayout = useCallback(() => {
    if (layoutTimeoutRef.current) {
      clearTimeout(layoutTimeoutRef.current);
      layoutTimeoutRef.current = null;
    }
    if (layoutRef.current) {
      layoutRef.current.kill();
      layoutRef.current = null;
    }
  }, []);

  const runLayout = useCallback(
    (graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes>) => {
      killLayout();

      (async () => {
        const [{ default: FA2Layout }, { default: forceAtlas2 }] =
          await Promise.all([
            import("graphology-layout-forceatlas2/worker"),
            import("graphology-layout-forceatlas2"),
          ]);

        const inferred = forceAtlas2.inferSettings(graph);
        const settings = { ...inferred, ...getFA2Settings(graph.order) };

        const layout = new FA2Layout(graph, { settings });
        layoutRef.current = layout;
        layout.start();
        setIsRunning(true);

        layoutTimeoutRef.current = setTimeout(async () => {
          layout.stop();
          layout.kill();
          layoutRef.current = null;
          layoutTimeoutRef.current = null;

          const { default: noverlap } = await import(
            "graphology-layout-noverlap"
          );
          noverlap.assign(graph, NOVERLAP_SETTINGS);
          options.sigma?.refresh();
          setIsRunning(false);
        }, getLayoutDuration(graph.order));
      })();
    },
    [killLayout, options.sigma],
  );

  // Start layout when enabled and graph is ready
  useEffect(() => {
    if (options.enabled && options.graph && options.graph.order > 0) {
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
