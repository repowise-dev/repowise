"use client";

import { useState } from "react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import { DollarSign } from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { StatCard } from "@repowise-dev/ui/shared/stat-card";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@repowise-dev/ui/ui/tabs";
import {
  CacheHitRatioCard,
  CostHeatmap,
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
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <DollarSign className="h-5 w-5 text-green-500" />
          Cost Tracking
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          LLM token usage and spend across all generation runs.
        </p>
      </div>

      {loadingSummary ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      ) : summary ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard
            label="Total Cost"
            value={formatCost(summary.total_cost_usd)}
            description="all time"
            icon={<DollarSign className="h-4 w-4 text-green-500" />}
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

      <Tabs value={tab} onValueChange={setTab} className="w-full">
        <TabsList>
          <TabsTrigger value="daily">Daily</TabsTrigger>
          <TabsTrigger value="cache">Cache & savings</TabsTrigger>
          <TabsTrigger value="hotspots">Hotspots</TabsTrigger>
          <TabsTrigger value="providers">Providers</TabsTrigger>
          <TabsTrigger value="operations">Operations</TabsTrigger>
        </TabsList>

        <TabsContent value="daily" className="mt-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Daily Spend (USD)</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              {loadingDay ? (
                <Skeleton className="h-48 w-full" />
              ) : dayGroups && dayGroups.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart
                    data={[...dayGroups].sort((a, b) => a.group.localeCompare(b.group))}
                    margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                  >
                    <XAxis
                      dataKey="group"
                      tick={{ fill: "var(--color-text-tertiary)", fontSize: 11 }}
                      axisLine={false}
                      tickLine={false}
                      interval="preserveStartEnd"
                      minTickGap={24}
                      tickFormatter={(v: string) => {
                        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
                        return m ? `${Number(m[2])}/${Number(m[3])}` : v;
                      }}
                    />
                    <YAxis
                      tick={{ fill: "var(--color-text-tertiary)", fontSize: 10 }}
                      axisLine={false}
                      tickLine={false}
                      tickFormatter={(v: number) => `$${v.toFixed(3)}`}
                    />
                    <Tooltip
                      cursor={{ fill: "var(--color-bg-elevated)" }}
                      contentStyle={{
                        background: "var(--color-bg-overlay)",
                        border: "1px solid var(--color-border-default)",
                        borderRadius: "6px",
                        fontSize: "12px",
                        color: "var(--color-text-primary)",
                      }}
                      formatter={(value: number) => [formatCost(value), "Cost"]}
                      labelFormatter={(label: string) => `Date: ${label}`}
                    />
                    <Bar dataKey="cost_usd" fill="var(--color-accent-primary)" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <p className="text-sm text-[var(--color-text-secondary)] py-8 text-center">
                  No cost data available.
                </p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="cache" className="mt-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <DistillSavingsCard data={distillSavings} />
            <CacheHitRatioCard />
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Token efficiency</CardTitle>
              </CardHeader>
              <CardContent className="pt-0">
                {summary ? (
                  <div className="space-y-2 text-xs text-[var(--color-text-secondary)]">
                    <div className="flex justify-between">
                      <span>Avg input tokens / call</span>
                      <span className="tabular-nums text-[var(--color-text-primary)]">
                        {summary.total_calls > 0
                          ? Math.round(summary.total_input_tokens / summary.total_calls).toLocaleString()
                          : "—"}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span>Avg output tokens / call</span>
                      <span className="tabular-nums text-[var(--color-text-primary)]">
                        {summary.total_calls > 0
                          ? Math.round(summary.total_output_tokens / summary.total_calls).toLocaleString()
                          : "—"}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span>Avg cost / call</span>
                      <span className="tabular-nums text-[var(--color-text-primary)]">
                        {summary.total_calls > 0
                          ? formatCost(summary.total_cost_usd / summary.total_calls)
                          : "—"}
                      </span>
                    </div>
                  </div>
                ) : (
                  <Skeleton className="h-24 w-full" />
                )}
              </CardContent>
            </Card>
          </div>
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
    </div>
  );
}
