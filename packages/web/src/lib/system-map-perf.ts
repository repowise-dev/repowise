/**
 * Performance marks for the System Map page: `sm:data` when the graph arrives,
 * then `sm:aggregate`, `sm:layout` and `sm:paint` from the map's phase
 * callback, plus one `sm:longtask` measure per main-thread task over 50 ms
 * while the page is open. Everything stays on the browser's own performance
 * timeline (the Performance panel, or `performance.getEntriesByName`); nothing
 * is sent anywhere. Each name keeps at most `SYSTEM_MAP_PERF_CAP` entries, so a
 * long session does not grow the timeline without bound.
 */

import { useEffect } from "react";
import type { SystemMapPhase } from "@repowise-dev/ui/workspace/system-map";

export type SystemMapMark = "data" | SystemMapPhase;

/** Entries kept per `sm:*` name; reaching it clears that name and starts again. */
export const SYSTEM_MAP_PERF_CAP = 100;

/** Clears a name's entries once it holds the cap, so the next one fits under it. */
function makeRoom(name: string, type: "mark" | "measure"): void {
  if (performance.getEntriesByName(name, type).length < SYSTEM_MAP_PERF_CAP) return;
  if (type === "mark") performance.clearMarks(name);
  else performance.clearMeasures(name);
}

export function markSystemMap(mark: SystemMapMark): void {
  if (typeof performance === "undefined" || typeof performance.mark !== "function") return;
  makeRoom(`sm:${mark}`, "mark");
  performance.mark(`sm:${mark}`);
}

/** Starts recording long tasks; returns the stop function. A no-op where unsupported. */
export function observeLongTasks(): () => void {
  if (
    typeof PerformanceObserver === "undefined" ||
    !PerformanceObserver.supportedEntryTypes?.includes("longtask")
  ) {
    return () => {};
  }
  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      makeRoom("sm:longtask", "measure");
      performance.measure("sm:longtask", { start: entry.startTime, duration: entry.duration });
    }
  });
  observer.observe({ type: "longtask" });
  return () => observer.disconnect();
}

/** Marks `sm:data` each time a graph arrives and records long tasks while mounted. */
export function useSystemMapPerfMarks(graph: unknown): void {
  useEffect(() => observeLongTasks(), []);
  useEffect(() => {
    if (graph) markSystemMap("data");
  }, [graph]);
}
