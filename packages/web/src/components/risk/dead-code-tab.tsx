"use client";

import { useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Button } from "@repowise-dev/ui/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { SummaryBar } from "@repowise-dev/ui/dead-code/summary-bar";
import { SafeToDeletePile } from "@repowise-dev/ui/dead-code/safe-to-delete-pile";
import { OwnerLeaderboard } from "@repowise-dev/ui/dead-code/owner-leaderboard";
import { FindingsBreakdownGrid } from "@repowise-dev/ui/dead-code/findings-breakdown-grid";
import { FindingsTable } from "@/components/dead-code/findings-table";
import { getDeadCodeSummary, listDeadCode, analyzeDeadCode } from "@/lib/api/dead-code";
import type { DeadCodeFindingResponse, DeadCodeSummaryResponse } from "@/lib/api/types";

export function DeadCodeTab({ repoId }: { repoId: string }) {
  const [analyzing, setAnalyzing] = useState(false);

  const { data: summary, isLoading: loadingSummary, error: summaryError, mutate: mutateSummary } =
    useSWR<DeadCodeSummaryResponse>(
      `dead-code-summary:${repoId}`,
      () => getDeadCodeSummary(repoId),
      { revalidateOnFocus: false },
    );

  const { data: findings } = useSWR<DeadCodeFindingResponse[]>(
    `dead-code-findings:${repoId}:all`,
    // Pull a wide slice so the leaderboard / pile / matrix have enough data
    // to be representative without paging.
    () => listDeadCode(repoId, { limit: 500 }),
    { revalidateOnFocus: false },
  );

  const findingsList = findings ?? [];
  const safeFindings = findingsList.filter((f) => f.safe_to_delete);

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

      {findings && safeFindings.length > 0 && (
        <SafeToDeletePile findings={safeFindings} reclaimableLines={summary?.deletable_lines} />
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Owner leaderboard</CardTitle>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              Reclaimable lines per primary contributor — who has the most cleanup leverage.
            </p>
          </CardHeader>
          <CardContent className="pt-0">
            <OwnerLeaderboard findings={findingsList} safeOnly />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Confidence × kind</CardTitle>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              Where the findings cluster — start with high-confidence cells.
            </p>
          </CardHeader>
          <CardContent className="pt-0">
            <FindingsBreakdownGrid findings={findingsList} />
          </CardContent>
        </Card>
      </div>

      <FindingsTable repoId={repoId} />
    </div>
  );
}
