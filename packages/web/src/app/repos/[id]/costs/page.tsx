"use client";

import { useState } from "react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import { DollarSign } from "lucide-react";
import { StatCard } from "@repowise-dev/ui/shared/stat-card";
import { MetricCard } from "@repowise-dev/ui/shared/metric-card";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { Tabs, ScrollableTabsList, TabsTrigger, TabsContent } from "@repowise-dev/ui/ui/tabs";
import {
  CostHeatmap,
  DailySpendChart,
  DistillSavingsCard,
  ProviderComparison,
  OperationBreakdown,
} from "@repowise-dev/ui/costs";
import { listCosts, getCostSummary, getDistillSavings } from "@/lib/api/costs";
import type { CostGroup, CostSummary, DistillSavings } from "@/lib/api/costs";
import { formatCost, formatNumber, formatTokens } from "@repowise-dev/ui/lib/format";

export default function CostsPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [tab, setTab] = useState("daily");

  const { data: summary, isLoading: loadingSummary } = useSWR<CostSummary>(
    `costs-summary:${id}`,
    () => getCostSummary(id),
    { revalidateOnFocus: false },
  );

  const { data: dayGroups, isLoading: loadingDay } = useSWR<CostGroup[]>(
    `costs-groups:${id}:day`,
    () => listCosts(id, { by: "day" }),
    { revalidateOnFocus: false },
  );

  const { data: modelGroups } = useSWR<CostGroup[]>(
    `costs-groups:${id}:model`,
    () => listCosts(id, { by: "model" }),
    { revalidateOnFocus: false },
  );

  const { data: opGroups } = useSWR<CostGroup[]>(
    `costs-groups:${id}:operation`,
    () => listCosts(id, { by: "operation" }),
    { revalidateOnFocus: false },
  );

  const { data: distillSavings } = useSWR<DistillSavings>(
    `distill-savings:${id}`,
    () => getDistillSavings(id),
    { revalidateOnFocus: false },
  );

  return (
    <PageShell
      maxWidth="wide"
      icon={<DollarSign className="h-5 w-5 text-[var(--color-success)]" />}
      title="Cost Tracking"
      description="What repowise saved your coding agent — and what generating the docs cost."
    >
      {/* Hero: the honest results surface — all tokens & dollars saved for the
          coding agent, across distill (CLI + hook) and MCP tool responses. */}
      <DistillSavingsCard data={distillSavings} />

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
              {loadingDay ? (
                <Skeleton className="h-48 w-full" />
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
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-24 w-full rounded-lg" />
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="hotspots" className="mt-4">
          {opGroups ? (
            <CostHeatmap
              groups={opGroups.map((g) => ({ group: g.group, cost_usd: g.cost_usd, calls: g.calls }))}
              title="Cost concentration by operation"
              emptyHint="No operation-level data yet."
            />
          ) : (
            <Skeleton className="h-40 w-full" />
          )}
        </TabsContent>

        <TabsContent value="providers" className="mt-4">
          {modelGroups ? (
            <ProviderComparison modelGroups={modelGroups} />
          ) : (
            <Skeleton className="h-40 w-full" />
          )}
        </TabsContent>

        <TabsContent value="operations" className="mt-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Spend by operation</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              {opGroups ? (
                <OperationBreakdown groups={opGroups} />
              ) : (
                <Skeleton className="h-40 w-full" />
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
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full rounded-lg" />
            ))}
          </div>
        ) : summary ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard
              label="Indexing cost"
              value={formatCost(summary.total_cost_usd)}
              description="across all generation runs"
              icon={<DollarSign className="h-4 w-4 text-[var(--color-success)]" />}
            />
            <StatCard
              label="Total Calls"
              value={formatNumber(summary.total_calls)}
              description="LLM API calls"
            />
            <StatCard
              label="Input Tokens"
              value={formatTokens(summary.total_input_tokens)}
              description="prompt tokens"
            />
            <StatCard
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
