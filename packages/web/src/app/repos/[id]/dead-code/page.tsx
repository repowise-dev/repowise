"use client";

import { useState } from "react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@repowise-dev/ui/ui/button";
import { SummaryBar } from "@repowise-dev/ui/dead-code/summary-bar";
import { FindingsTable } from "@/components/dead-code/findings-table";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { getDeadCodeSummary, analyzeDeadCode } from "@/lib/api/dead-code";
import type { DeadCodeSummaryResponse } from "@/lib/api/types";

export default function DeadCodePage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [analyzing, setAnalyzing] = useState(false);

  const { data: summary, isLoading: loadingSummary, error: summaryError, mutate: mutateSummary } = useSWR<DeadCodeSummaryResponse>(
    `dead-code-summary:${id}`,
    () => getDeadCodeSummary(id),
    { revalidateOnFocus: false },
  );

  const handleAnalyze = async () => {
    setAnalyzing(true);
    try {
      await analyzeDeadCode(id);
      toast.success("Analysis started â€” results will appear shortly.");
    } catch (err) {
      toast.error(
        err instanceof Error ? `Couldn't start analysis: ${err.message}` : "Couldn't start analysis",
      );
    } finally {
      setAnalyzing(false);
    }
  };

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
            <Trash2 className="h-5 w-5 text-red-500" />
            Dead Code
          </h1>
          <p className="text-sm text-[var(--color-text-secondary)]">
            Unused files, exports, and zombie packages.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button
            size="sm"
            variant="outline"
            onClick={handleAnalyze}
            disabled={analyzing}
          >
            {analyzing ? "Startingâ€¦" : "Analyze"}
          </Button>
        </div>
      </div>

      {loadingSummary ? (
        <div className="grid grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      ) : summary ? (
        <SummaryBar summary={summary} />
      ) : summaryError ? (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)] flex items-center justify-between gap-2">
          <span>Couldn&apos;t load summary.</span>
          <Button size="sm" variant="outline" onClick={() => mutateSummary()}>
            Retry
          </Button>
        </div>
      ) : null}

      <FindingsTable repoId={id} />
    </div>
  );
}
