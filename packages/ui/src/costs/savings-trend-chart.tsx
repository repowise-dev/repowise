"use client";

import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { formatTokens } from "../lib/format";

export interface SavingsTrendChartProps {
  /** Day-grouped savings rows ({ group: "YYYY-MM-DD", saved_tokens }). The
   *  `per_day` series from /distill-savings — total agent tokens saved per day
   *  (distill + MCP counterfactual). Caller fetches. */
  groups: Array<{ group: string; saved_tokens: number }>;
  height?: number;
}

/**
 * Daily agent-savings bars — the token counterpart to DailySpendChart, so the
 * Costs page can show savings accruing over time, not just a lifetime total.
 * Renders the `per_day` array that the page already fetches.
 */
export function SavingsTrendChart({ groups, height = 220 }: SavingsTrendChartProps) {
  const data = [...groups]
    .filter((g) => g.saved_tokens > 0)
    .sort((a, b) => a.group.localeCompare(b.group));

  if (data.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-secondary)] py-8 text-center">
        No savings recorded yet.
      </p>
    );
  }

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
          tickFormatter={(v: number) => formatTokens(v)}
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
          formatter={(value) => [`${formatTokens(Number(value))} tokens`, "Saved"]}
          labelFormatter={(label) => `Date: ${String(label)}`}
        />
        <Bar
          dataKey="saved_tokens"
          fill="var(--color-savings-distill)"
          radius={[4, 4, 0, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
