"use client";

import { useCallback, useRef } from "react";
import type { LoadLinksBetween } from "@repowise-dev/ui/workspace/system-map";
import { getWorkspaceLinksBetween } from "@/lib/api/workspace";

/** Pages kept for the page's lifetime; the oldest goes first past this. */
const CACHE_LIMIT = 64;

/**
 * The map's links loader: one page of routes between two services, resolved
 * server-side. Pages are cached by request, so reselecting an edge or going
 * back to a pair asks nothing twice; a failed request is not cached.
 */
export function useServiceLinks(): LoadLinksBetween {
  const cache = useRef(new Map<string, ReturnType<LoadLinksBetween>>());
  return useCallback<LoadLinksBetween>((a, b, opts) => {
    const key = JSON.stringify([a, b, opts.kind ?? "", opts.q ?? "", opts.limit ?? 0, opts.offset ?? 0]);
    const pages = cache.current;
    const hit = pages.get(key);
    if (hit) return hit;
    const request = getWorkspaceLinksBetween({ a, b, ...opts });
    pages.set(key, request);
    request.catch(() => pages.delete(key));
    if (pages.size > CACHE_LIMIT) pages.delete(pages.keys().next().value!);
    return request;
  }, []);
}
