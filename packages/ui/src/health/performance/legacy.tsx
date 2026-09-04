"use client";

import useSWR from "swr";
import { Gauge } from "lucide-react";
import type { HealthFinding } from "@repowise-dev/types/health";

import { EmptyState } from "../../shared/empty-state";
import { Skeleton, SkeletonRegion } from "../../ui/skeleton";
import { BiomarkerList } from "../biomarker-list";
import type { PerformanceViewAdapter } from "./adapter";

/**
 * The fallback for a server that predates causal grouping.
 *
 * It shows raw findings and says so. Inventing groups, ranks, or plan links
 * from findings alone would put this surface's vocabulary on data that never
 * carried it.
 */
export function LegacyPerformanceFindings({ adapter }: { adapter: PerformanceViewAdapter }) {
  const { data, error, isLoading } = useSWR<HealthFinding[]>(
    `performance-findings-legacy:${adapter.cacheKey}`,
    () => adapter.listFindings({ dimension: "performance", limit: 100 }),
    { revalidateOnFocus: false },
  );
  if (isLoading)
    return (
      <SkeletonRegion label="Loading performance findings">
        <Skeleton className="h-72 rounded" />
      </SkeletonRegion>
    );
  if (error || !data?.length) {
    return (
      <EmptyState
        icon={<Gauge className="h-6 w-6" />}
        title="No performance opportunities"
        description="This server does not provide causal grouping. Reindex with a current server to tell an empty result apart from unsupported grouping."
      />
    );
  }
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">
          Raw performance findings
        </h2>
        <p className="mt-1 max-w-[72ch] text-sm text-[var(--color-text-secondary)]">
          This server predates causal opportunity groups. Up to 100 canonical findings are shown
          without inventing plan links or rank semantics.
        </p>
      </div>
      <BiomarkerList
        findings={data}
        grouped
        onSelect={(finding) => adapter.navigate(adapter.fileHref(finding.file_path))}
      />
    </div>
  );
}
