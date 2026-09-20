"use client";

import useSWR from "swr";
import {
  getHealthFileBreakdown,
  type HealthFileBreakdownResponse,
} from "@/lib/api/code-health";
import type { HealthCounts } from "@repowise-dev/types/health";

export function useFileBreakdown(
  repoId: string,
  filePath: string | null,
  counts?: HealthCounts,
) {
  // Counts is in the key as well as the query: the drawer opens from a row
  // whose score was read under it, and a cached breakdown from the other
  // reading would answer a click on one number with a different one.
  const suffix = counts && counts !== "everything" ? `:${counts}` : "";
  return useSWR<HealthFileBreakdownResponse>(
    filePath ? `code-health-breakdown:${repoId}:${filePath}${suffix}` : null,
    () => getHealthFileBreakdown(repoId, filePath!, counts),
    { revalidateOnFocus: false },
  );
}
