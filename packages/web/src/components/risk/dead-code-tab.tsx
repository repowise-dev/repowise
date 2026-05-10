"use client";

import { useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Button } from "@repowise-dev/ui/ui/button";
import { SummaryBar } from "@repowise-dev/ui/dead-code/summary-bar";
import { FindingsTable } from "@/components/dead-code/findings-table";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { getDeadCodeSummary, analyzeDeadCode } from "@/lib/api/dead-code";
import type { DeadCodeSummaryResponse } from "@/lib/api/types";

export function DeadCodeTab({ repoId }: { repoId: string }) {
  const [analyzing, setAnalyzing] = useState(false);

  const { data: summary, isLoading, error, mutate } = useSWR<DeadCodeSummaryResponse>(
    `dead-code-summary:${repoId}`,
    () => getDeadCodeSummary(repoId),
    { revalidateOnFocus: false },
  );

  const handleAnalyze = async () => {
    setAnalyzing(true);
    try {
      await analyzeDeadCode(repoId);
      toast.success("Analysis started — results will appear shortly.");
    } catch (err) {
      toast.error(
        err instanceof Error ? `Couldn't start analysis: ${err.message}` : "Couldn't start analysis",
      );
    } finally {
      setAnalyzing(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-end">
        <Button size="sm" variant="outline" onClick={handleAnalyze} disabled={analyzing}>
          {analyzing ? "Starting…" : "Re-analyze"}
        </Button>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      ) : summary ? (
        <SummaryBar summary={summary} />
      ) : error ? (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)] flex items-center justify-between gap-2">
          <span>Couldn&apos;t load summary.</span>
          <Button size="sm" variant="outline" onClick={() => mutate()}>
            Retry
          </Button>
        </div>
      ) : null}

      <FindingsTable repoId={repoId} />
    </div>
  );
}
