"use client";

/**
 * SWR-backed hooks for the C4 endpoints. One hook per level so consumers
 * can opt into only what they render — L1 view doesn't need to fetch L3.
 */

import useSWR from "swr";
import type { C4L1, C4L2, C4L3 } from "@repowise-dev/ui/c4";
import { getC4L1, getC4L2, getC4L3 } from "@/lib/api/c4";

const SWR_OPTS = { revalidateOnFocus: false, revalidateOnReconnect: false };

export function useC4L1(repoId: string | null) {
  const { data, error, isLoading } = useSWR<C4L1>(
    repoId ? `c4-l1:${repoId}` : null,
    () => getC4L1(repoId!),
    SWR_OPTS,
  );
  return { view: data ?? null, error: (error as Error | undefined) ?? null, isLoading };
}

export function useC4L2(repoId: string | null) {
  const { data, error, isLoading } = useSWR<C4L2>(
    repoId ? `c4-l2:${repoId}` : null,
    () => getC4L2(repoId!),
    SWR_OPTS,
  );
  return { view: data ?? null, error: (error as Error | undefined) ?? null, isLoading };
}

export function useC4L3(repoId: string | null, containerId: string | null) {
  const { data, error, isLoading } = useSWR<C4L3>(
    repoId && containerId ? `c4-l3:${repoId}:${containerId}` : null,
    () => getC4L3(repoId!, containerId!),
    SWR_OPTS,
  );
  return { view: data ?? null, error: (error as Error | undefined) ?? null, isLoading };
}
