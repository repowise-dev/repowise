"use client";

import { useCallback, useSyncExternalStore } from "react";
import {
  activeNewRoutes,
  navOwnsRoute,
  readNewSurfaces,
  subscribeNewSurfaces,
  tabOwnsRoute,
} from "@/lib/release-moment";

// The snapshot is the joined route list, a string, so an unchanged value keeps
// its identity and the nav does not re-render on every storage event.
function snapshot(): string {
  return activeNewRoutes(readNewSurfaces(), Date.now()).join("\n");
}

function useActiveRoutes(): string {
  return useSyncExternalStore(subscribeNewSurfaces, snapshot, () => "");
}

/** Whether a nav entry leads to a page the latest upgrade highlighted. */
export function useIsNewSurface(): (href: string, exact?: boolean) => boolean {
  const joined = useActiveRoutes();
  return useCallback(
    (href: string, exact = false) =>
      !!joined && joined.split("\n").some((r) => navOwnsRoute(r, href, exact)),
    [joined],
  );
}

/** Whether a tab's URL (`/repos/abc/code-health?tab=performance`) is one the
 *  latest upgrade highlighted. */
export function useIsNewTab(): (location: string) => boolean {
  const joined = useActiveRoutes();
  return useCallback(
    (location: string) => !!joined && joined.split("\n").some((r) => tabOwnsRoute(r, location)),
    [joined],
  );
}
