"use client";

import useSWR from "swr";
import {
  getHealthFileBreakdown,
  type HealthFileBreakdownResponse,
} from "@/lib/api/code-health";

export function useFileBreakdown(repoId: string, filePath: string | null) {
  return useSWR<HealthFileBreakdownResponse>(
    filePath ? `code-health-breakdown:${repoId}:${filePath}` : null,
    () => getHealthFileBreakdown(repoId, filePath!),
    { revalidateOnFocus: false },
  );
}
