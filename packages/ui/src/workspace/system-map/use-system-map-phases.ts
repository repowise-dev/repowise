"use client";

/**
 * Reports when the map finishes each render phase, so a host can time them
 * (performance marks, a trace) without this package knowing how. Observes the
 * layout hook's outputs only; it never changes what is drawn.
 */

import { useEffect, useRef } from "react";

/**
 * `aggregate`: a new drawn graph is computed (every view change). `layout`: a
 * layout run finished (not reported for a cached layout or a kind toggle,
 * which run none). `paint`: the frame after that layout was painted.
 */
export type SystemMapPhase = "aggregate" | "layout" | "paint";

export function useSystemMapPhases(
  onPhase: ((phase: SystemMapPhase) => void) | undefined,
  viewGraph: { nodes: readonly unknown[] } | null,
  layoutLoading: boolean,
): void {
  const notify = useRef(onPhase);
  notify.current = onPhase;
  const wasLoading = useRef(layoutLoading);
  const drawn = useRef(viewGraph);
  drawn.current = viewGraph;

  useEffect(() => {
    if (viewGraph) notify.current?.("aggregate");
  }, [viewGraph]);

  // Keyed on the loading flag falling, not on the view graph: the layout hook
  // raises it a commit after a new graph arrives, so the graph alone would
  // report a layout that has not run yet. A null or empty graph clears the
  // flag without a run, so it reports nothing.
  useEffect(() => {
    const settled = wasLoading.current && !layoutLoading && (drawn.current?.nodes.length ?? 0) > 0;
    wasLoading.current = layoutLoading;
    if (!settled || !notify.current) return;
    notify.current("layout");
    if (typeof requestAnimationFrame !== "function") return;
    // The second frame callback runs after the first frame was painted.
    let inner = 0;
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => notify.current?.("paint"));
    });
    return () => {
      cancelAnimationFrame(outer);
      cancelAnimationFrame(inner);
    };
  }, [layoutLoading]);
}
