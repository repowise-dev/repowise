"use client";

import useSWR from "swr";
import type { C4L1, C4L2, C4L3 } from "@repowise-dev/ui/c4";
import { getC4L1, getC4L2, getC4L3 } from "@/lib/api/c4";

const SWR_OPTS = { revalidateOnFocus: false, revalidateOnReconnect: false };

/**
 * The context and container views, loaded together: the context names the
 * system and the containers feed the component level's picker.
 */
export function useC4Context(repoId: string) {
  const l1 = useSWR<C4L1>(`c4-l1:${repoId}`, () => getC4L1(repoId), SWR_OPTS);
  const l2 = useSWR<C4L2>(`c4-l2:${repoId}`, () => getC4L2(repoId), SWR_OPTS);
  return { l1, l2 };
}

/** One container's components; no request until a container is given. */
export function useC4Components(repoId: string, containerId: string | null) {
  return useSWR<C4L3>(
    containerId ? `c4-l3:${repoId}:${containerId}` : null,
    () => getC4L3(repoId, containerId!),
    SWR_OPTS,
  );
}
