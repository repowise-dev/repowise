"use client";

import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { CHART_HEIGHT } from "./chart-height";
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
export function DailySpendChart({ groups, height = CHART_HEIGHT }: DailySpendChartProps) {
  if (groups.length === 0) {
    // Sized to the plot box it stands in for. Collapsing to two lines of
    // text snapped the card up ~150px the moment an empty response landed,
    // which reads as the page breaking rather than as "nothing here yet".
    return (
      <div
        className="flex items-center justify-center text-center text-sm text-[var(--color-text-secondary)]"
        style={{ height }}
      >
        No cost data available.
      </div>
    );
  }

  const data = [...groups].sort((a, b) => a.group.localeCompare(b.group));

  // The plot box is reserved before recharts measures it. A bare
  // ResponsiveContainer renders nothing until its ResizeObserver fires, so
  // the card grew into place on every mount.
  return (
    <div style={{ height: height }}>
      <ResponsiveContainer width="100%" height="100%">
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
    </div>
  );
}
