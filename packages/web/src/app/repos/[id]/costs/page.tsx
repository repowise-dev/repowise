"use client";

import { useState } from "react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import { DollarSign } from "lucide-react";
import { MetricCard } from "@repowise-dev/ui/shared/metric-card";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { ChartSkeleton, StatGridSkeleton } from "@repowise-dev/ui/shared/loading-skeletons";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { SkeletonRegion, Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { Tabs, ScrollableTabsList, TabsTrigger, TabsContent } from "@repowise-dev/ui/ui/tabs";
import { CHART_HEIGHT, operationBreakdownHeight } from "@repowise-dev/ui/costs/chart-height";
import {
  CostHeatmap,
  DailySpendChart,
  DistillSavingsCard,
  ProviderComparison,
  OperationBreakdown,
  RoiCard,
  SavingsTrendChart,
} from "@repowise-dev/ui/costs";
import { listCosts, getCostSummary, getDistillSavings } from "@/lib/api/costs";
import type { CostGroup, CostSummary, DistillSavings } from "@/lib/api/costs";
import { formatCost, formatNumber, formatTokens } from "@repowise-dev/ui/lib/format";

export default function CostsPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [tab, setTab] = useState("daily");

  const {
    data: summary,
    error: summaryError,
    isLoading: loadingSummary,
    mutate: retrySummary,
  } = useSWR<CostSummary>(
    `costs-summary:${id}`,
    () => getCostSummary(id),
    { revalidateOnFocus: false },
  );

  const {
    data: dayGroups,
    error: dayError,
    isLoading: loadingDay,
    mutate: retryDay,
  } = useSWR<CostGroup[]>(
    `costs-groups:${id}:day`,
    () => listCosts(id, { by: "day" }),
    { revalidateOnFocus: false },
  );

  const {
    data: modelGroups,
    error: modelError,
    mutate: retryModel,
  } = useSWR<CostGroup[]>(
    `costs-groups:${id}:model`,
    () => listCosts(id, { by: "model" }),
    { revalidateOnFocus: false },
  );

  const {
    data: opGroups,
    error: opError,
    mutate: retryOp,
  } = useSWR<CostGroup[]>(
    `costs-groups:${id}:operation`,
    () => listCosts(id, { by: "operation" }),
    { revalidateOnFocus: false },
  );

  const {
    data: distillSavings,
    error: distillError,
    mutate: retryDistill,
  } = useSWR<DistillSavings>(
    `distill-savings:${id}`,
    () => getDistillSavings(id),
    { revalidateOnFocus: false },
  );

  // A discriminated union rather than a bare string, so the populated branch
  // carries the data it is populated with and TypeScript can see that.
  const savings = distillError
    ? ({ kind: "error" } as const)
    : distillSavings === undefined
      ? ({ kind: "pending" } as const)
      : distillSavings.available
        ? ({ kind: "populated", data: distillSavings } as const)
        : ({ kind: "unavailable" } as const);

  return (
    <PageShell
      maxWidth="wide"
      icon={<DollarSign className="h-5 w-5 text-[var(--color-success)]" />}
      title="Cost Tracking"
      description="What repowise saved your coding agent — and what generating the docs cost."
    >
      {/* Hero: the honest results surface — all tokens & dollars saved for the
          coding agent, across distill (CLI + hook) and MCP tool responses. */}
      {distillError ? (
        <Card>
          <CardContent className="py-6">
            <ApiError
              title="Couldn't load agent savings"
              message="The savings endpoint did not respond. Everything below is unaffected."
              onRetry={() => void retryDistill()}
            />
          </CardContent>
        </Card>
      ) : (
        <DistillSavingsCard data={distillSavings} />
      )}

      {/* ROI and the savings trend are gated on `available`, which is a
          SEMANTIC state, not a load state: a repo that has never run distill
          legitimately has neither card. Three states, not a boolean, because
          a plain `available &&` renders nothing while the request is still in
          flight and then inserts ~400px above the tabs on arrival.

          pending     → reserve the populated height
          unavailable → render nothing, reserve nothing
          populated   → the cards */}
      {savings.kind === "pending" ? (
        <>
          <Skeleton className="h-[104px] w-full rounded-xl" />
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Agent tokens saved by day</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <ChartSkeleton height={CHART_HEIGHT} label="Loading savings trend" />
            </CardContent>
          </Card>
        </>
      ) : savings.kind === "populated" ? (
        <>
          {summary ? (
            <RoiCard
              savedUsd={savings.data.estimated_usd_saved}
              spentUsd={summary.total_cost_usd}
              savedTokens={savings.data.saved_tokens + savings.data.mcp_tokens}
            />
          ) : null}
          {(savings.data.per_day?.length ?? 0) > 0 ? (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Agent tokens saved by day</CardTitle>
              </CardHeader>
              <CardContent className="pt-0">
                <SavingsTrendChart groups={savings.data.per_day} />
              </CardContent>
            </Card>
          ) : null}
        </>
      ) : null}

      <Tabs value={tab} onValueChange={setTab} className="w-full">
        <ScrollableTabsList>
          <TabsTrigger value="daily">Daily</TabsTrigger>
          <TabsTrigger value="operations">Spend by operation</TabsTrigger>
          <TabsTrigger value="providers">Providers</TabsTrigger>
          <TabsTrigger value="hotspots">Hotspots</TabsTrigger>
          <TabsTrigger value="efficiency">Efficiency</TabsTrigger>
        </ScrollableTabsList>

        <TabsContent value="daily" className="mt-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Daily Spend (USD)</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              {dayError ? (
                <ApiError
                  title="Couldn't load daily spend"
                  onRetry={() => void retryDay()}
                />
              ) : loadingDay ? (
                <ChartSkeleton height={CHART_HEIGHT} label="Loading daily spend" />
              ) : (
                <DailySpendChart groups={dayGroups ?? []} />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Cache analytics aren't wired to real data yet; the tab shows the
            real per-call efficiency numbers as a compact stat strip. */}
        <TabsContent value="efficiency" className="mt-4">
          {summary ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <MetricCard
                label="Avg input / call"
                value={
                  summary.total_calls > 0
                    ? Math.round(summary.total_input_tokens / summary.total_calls).toLocaleString()
                    : "—"
                }
              />
              <MetricCard
                label="Avg output / call"
                value={
                  summary.total_calls > 0
                    ? Math.round(summary.total_output_tokens / summary.total_calls).toLocaleString()
                    : "—"
                }
              />
              <MetricCard
                label="Avg cost / call"
                value={
                  summary.total_calls > 0
                    ? formatCost(summary.total_cost_usd / summary.total_calls)
                    : "—"
                }
              />
            </div>
          ) : summaryError ? (
            <ApiError
              title="Couldn't load efficiency"
              onRetry={() => void retrySummary()}
            />
          ) : (
            <StatGridSkeleton count={3} />
          )}
        </TabsContent>

        <TabsContent value="hotspots" className="mt-4">
          {opError ? (
            <ApiError title="Couldn't load hotspots" onRetry={() => void retryOp()} />
          ) : opGroups ? (
            <CostHeatmap
              groups={opGroups.map((g) => ({ group: g.group, cost_usd: g.cost_usd, calls: g.calls }))}
              title="Cost concentration by operation"
              emptyHint="No operation-level data yet."
            />
          ) : (
            // Heatmap cell heights are data-driven, so there is no height to
            // match. Reserve the floor and let it grow rather than asserting
            // a number that is wrong for every repo but one.
            <SkeletonRegion label="Loading hotspots">
              <Skeleton className="min-h-40 w-full" />
            </SkeletonRegion>
          )}
        </TabsContent>

        <TabsContent value="providers" className="mt-4">
          {modelError ? (
            <ApiError title="Couldn't load providers" onRetry={() => void retryModel()} />
          ) : modelGroups ? (
            <ProviderComparison modelGroups={modelGroups} />
          ) : (
            <SkeletonRegion label="Loading providers">
              <Skeleton className="min-h-40 w-full" />
            </SkeletonRegion>
          )}
        </TabsContent>

        <TabsContent value="operations" className="mt-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Spend by operation</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              {opError ? (
                <ApiError
                  title="Couldn't load spend by operation"
                  onRetry={() => void retryOp()}
                />
              ) : opGroups ? (
                <OperationBreakdown groups={opGroups} />
              ) : (
                // The chart's own floor, from its own formula: the row count
                // is unknown until the data lands, and the chart only grows
                // from here.
                <ChartSkeleton
                  height={operationBreakdownHeight(0)}
                  label="Loading spend by operation"
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Indexing / generation cost — deliberately secondary to the savings
          hero above. */}
      <div className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
          Indexing &amp; generation cost
        </p>
        {loadingSummary ? (
          <StatGridSkeleton
            count={4}
            columns="grid-cols-2 sm:grid-cols-4"
            description
          />
        ) : summaryError ? (
          <ApiError
            title="Couldn't load generation cost"
            onRetry={() => void retrySummary()}
          />
        ) : summary ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <MetricCard
              label="Indexing cost"
              value={formatCost(summary.total_cost_usd)}
              description="across all generation runs"
              icon={<DollarSign className="h-4 w-4 text-[var(--color-success)]" />}
            />
            <MetricCard
              label="Total Calls"
              value={formatNumber(summary.total_calls)}
              description="LLM API calls"
            />
            <MetricCard
              label="Input Tokens"
              value={formatTokens(summary.total_input_tokens)}
              description="prompt tokens"
            />
            <MetricCard
              label="Output Tokens"
              value={formatTokens(summary.total_output_tokens)}
              description="completion tokens"
            />
          </div>
        ) : null}
      </div>
    </PageShell>
  );
}
