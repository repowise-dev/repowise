"use client";

import useSWR from "swr";
import { useParams } from "next/navigation";
import { HeartPulse } from "lucide-react";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { Button } from "@repowise-dev/ui/ui/button";
import { HealthKpiCards } from "@repowise-dev/ui/health/kpi-cards";
import { HealthFileTable } from "@repowise-dev/ui/health/file-table";
import { BiomarkerList } from "@repowise-dev/ui/health/biomarker-list";
import { getHealthOverview, type HealthOverviewResponse } from "@/lib/api/code-health";

export default function HealthPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const { data, isLoading, error, mutate } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${id}`,
    () => getHealthOverview(id, 25),
    { revalidateOnFocus: false },
  );

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
            <HeartPulse className="h-5 w-5 text-emerald-500" />
            Code Health
          </h1>
          <p className="text-sm text-[var(--color-text-secondary)]">
            Per-file health scores from CCN, nesting, and brain-method biomarkers.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)] flex items-center justify-between gap-2">
          <span>Couldn&apos;t load health data. Run `repowise init` to populate.</span>
          <Button size="sm" variant="outline" onClick={() => mutate()}>
            Retry
          </Button>
        </div>
      ) : data ? (
        <>
          <HealthKpiCards summary={data.summary} />
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2">
              <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)] mb-2">
                Lowest-scoring files
              </h2>
              <HealthFileTable files={data.files} />
            </div>
            <div>
              <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)] mb-2">
                Top biomarker findings
              </h2>
              <BiomarkerList findings={data.top_findings} />
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
