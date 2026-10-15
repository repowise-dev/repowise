"use client";

import { useCallback, useState } from "react";
import type { JobOutcome } from "@repowise-dev/ui/jobs";

export interface WatchedJob<K extends string> {
  id: string;
  /** The action that started it, or `null` for a job found already running. */
  action: K | null;
}

/**
 * The job a panel shows in place of its action buttons. A clean finish clears
 * it; a failure or a finish with problems stays until dismissed, so its reason
 * and the retry and settings actions are not lost to a toast.
 */
export function useWatchedJob<K extends string>() {
  const [watched, setWatched] = useState<WatchedJob<K> | null>(null);
  const watch = useCallback((id: string, action: K | null) => setWatched({ id, action }), []);
  const dismiss = useCallback(() => setWatched(null), []);
  const settle = useCallback((outcome: JobOutcome) => {
    if (outcome === "completed" || outcome === "cancelled") setWatched(null);
  }, []);
  return { watched, watch, settle, dismiss };
}
