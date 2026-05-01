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
import { StatCard } from "@/components/shared/stat-card";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise/ui/ui/card";
import { Skeleton } from "@repowise/ui/ui/skeleton";
import { listCosts, getCostSummary } from "@/lib/api/costs";
import type { CostGroup, CostSummary } from "@/lib/api/costs";
import { formatCost, formatNumber, formatTokens } from "@/lib/utils/format";

type GroupBy = "day" | "model" | "operation";

const GROUP_BY_OPTIONS: { label: string; value: GroupBy }[] = [
  { label: "By Day", value: "day" },
  { label: "By Model", value: "model" },
  { label: "By Operation", value: "operation" },
];

export default function CostsPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [by, setBy] = useState<GroupBy>("day");

  const { data: summary, isLoading: loadingSummary } = useSWR<CostSummary>(
    `costs-summary:${id}`,
    () => getCostSummary(id),
    { revalidateOnFocus: false },
  );

  const { data: groups, isLoading: loadingGroups } = useSWR<CostGroup[]>(
    `costs-groups:${id}:${by}`,
    () => listCosts(id, { by }),
    { revalidateOnFocus: false },
  );

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <DollarSign className="h-5 w-5 text-green-500" />
          Cost Tracking
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          LLM token usage and spend across all generation runs.
        </p>
      </div>

      {/* Summary stat cards */}
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

      {/* Group-by selector */}
      <div className="flex items-center gap-2">
        {GROUP_BY_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => setBy(opt.value)}
            className={[
              "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              by === opt.value
                ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
            ].join(" ")}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Chart — only when grouping by day */}
      {by === "day" && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Daily Spend (USD)</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            {loadingGroups ? (
              <Skeleton className="h-48 w-full" />
            ) : groups && groups.length > 0 ? (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart
                  data={[...groups].sort((a, b) => a.group.localeCompare(b.group))}
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
                      // ISO date "YYYY-MM-DD" → "M/D"
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
      )}

      {/* Table */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            {by === "day" ? "Daily Breakdown" : by === "model" ? "By Model" : "By Operation"}
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          {loadingGroups ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </div>
          ) : groups && groups.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--color-border-default)] text-left">
                    <th className="pb-2 pr-4 font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider text-xs">
                      {by === "day" ? "Date" : by === "model" ? "Model" : "Operation"}
                    </th>
                    <th className="pb-2 pr-4 font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider text-xs text-right">
                      Calls
                    </th>
                    <th className="pb-2 pr-4 font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider text-xs text-right">
                      Input Tokens
                    </th>
                    <th className="pb-2 pr-4 font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider text-xs text-right">
                      Output Tokens
                    </th>
                    <th className="pb-2 font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider text-xs text-right">
                      Cost (USD)
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--color-border-default)]">
                  {(by === "day"
                    ? [...groups].sort((a, b) => b.group.localeCompare(a.group))
                    : groups
                  ).map((row) => (
                    <tr
                      key={row.group}
                      className="hover:bg-[var(--color-bg-elevated)] transition-colors"
                    >
                      <td className="py-2 pr-4 text-[var(--color-text-primary)] font-mono text-xs max-w-[280px]">
                        <span className="block truncate" title={row.group}>{row.group}</span>
                      </td>
                      <td className="py-2 pr-4 text-right text-[var(--color-text-secondary)] tabular-nums">
                        {formatNumber(row.calls)}
                      </td>
                      <td className="py-2 pr-4 text-right text-[var(--color-text-secondary)] tabular-nums">
                        {formatTokens(row.input_tokens)}
                      </td>
                      <td className="py-2 pr-4 text-right text-[var(--color-text-secondary)] tabular-nums">
                        {formatTokens(row.output_tokens)}
                      </td>
                      <td className="py-2 text-right font-medium text-[var(--color-text-primary)] tabular-nums">
                        {formatCost(row.cost_usd)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-[var(--color-text-secondary)] py-8 text-center">
              No cost data available for this repository.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
