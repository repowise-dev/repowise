"use client";

import useSWR from "swr";
import type { ArchitectureView } from "@repowise-dev/ui/c4";
import { getArchitectureView } from "@/lib/api/c4";

const SWR_OPTS = { revalidateOnFocus: false, revalidateOnReconnect: false };

export function useArchitectureView(repoId: string | null) {
  const { data, error, isLoading } = useSWR<ArchitectureView>(
    repoId ? `architecture-view:${repoId}` : null,
    () => getArchitectureView(repoId!),
    SWR_OPTS,
  );
  return { view: data ?? null, error: (error as Error | undefined) ?? null, isLoading };
}
