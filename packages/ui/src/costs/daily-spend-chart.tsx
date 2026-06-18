"use client";

import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { formatCost } from "../lib/format";

export interface DailySpendChartProps {
  /** Day-grouped cost rows ({ group: "YYYY-MM-DD", cost_usd }). Caller fetches. */
  groups: Array<{ group: string; cost_usd: number }>;
  height?: number;
}

/**
 * Daily generation-spend bars. A peer of OperationBreakdown / ProviderComparison
 * so the Costs page composes all charts from `ui` and they restyle on a bump.
 */
export function DailySpendChart({ groups, height = 220 }: DailySpendChartProps) {
  if (groups.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-secondary)] py-8 text-center">
        No cost data available.
      </p>
    );
  }

  const data = [...groups].sort((a, b) => a.group.localeCompare(b.group));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
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
          formatter={(value) => [formatCost(Number(value)), "Cost"]}
          labelFormatter={(label) => `Date: ${String(label)}`}
        />
        <Bar dataKey="cost_usd" fill="var(--color-accent-primary)" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
