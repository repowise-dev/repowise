"use client";

import { useCallback, useSyncExternalStore } from "react";
import {
  activeNewRoutes,
  readNewSurfaces,
  routeMatches,
  subscribeNewSurfaces,
} from "@/lib/release-moment";

// The snapshot is the joined route list, a string, so an unchanged value keeps
// its identity and the nav does not re-render on every storage event.
function snapshot(): string {
  return activeNewRoutes(readNewSurfaces(), Date.now()).join("\n");
}

/** Whether a nav href opens a page the latest upgrade highlighted. */
export function useIsNewSurface(): (href: string) => boolean {
  const joined = useSyncExternalStore(subscribeNewSurfaces, snapshot, () => "");
  return useCallback(
    (href: string) => !!joined && joined.split("\n").some((r) => routeMatches(r, href) && sameDepth(r, href)),
    [joined],
  );
}

// A nav item marks only its own page, not a parent of it: `/repos/:id` is not
// the chat page.
function sameDepth(route: string, href: string): boolean {
  return route.split("/").filter(Boolean).length === href.split("/").filter(Boolean).length;
}
