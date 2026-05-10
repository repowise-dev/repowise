"use client";

import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { formatCost } from "../lib/format";

export interface OperationBreakdownProps {
  groups: Array<{
    group: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  }>;
  /** Bar color (defaults to accent-primary). */
  color?: string;
  /** Override the dataKey used for bar height. */
  metric?: "cost_usd" | "calls" | "input_tokens" | "output_tokens";
}

const PALETTE = [
  "var(--color-accent-primary)",
  "var(--color-lang-python)",
  "var(--color-lang-typescript)",
  "var(--color-lang-go)",
  "var(--color-lang-rust)",
  "var(--color-lang-java)",
  "var(--color-lang-cpp)",
  "var(--color-lang-other)",
];

export function OperationBreakdown({ groups, metric = "cost_usd" }: OperationBreakdownProps) {
  const sorted = [...groups].sort((a, b) => (b[metric] ?? 0) - (a[metric] ?? 0)).slice(0, 12);
  if (sorted.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-secondary)] py-8 text-center">
        No data to break down.
      </p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={Math.max(180, sorted.length * 24 + 40)}>
      <BarChart
        data={sorted}
        layout="vertical"
        margin={{ top: 4, right: 16, bottom: 0, left: 8 }}
      >
        <XAxis
          type="number"
          tick={{ fill: "var(--color-text-tertiary)", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v: number) => (metric === "cost_usd" ? `$${v.toFixed(3)}` : String(v))}
        />
        <YAxis
          type="category"
          dataKey="group"
          tick={{ fill: "var(--color-text-secondary)", fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          width={140}
        />
        <Tooltip
          cursor={{ fill: "var(--color-bg-elevated)" }}
          contentStyle={{
            background: "var(--color-bg-overlay)",
            border: "1px solid var(--color-border-default)",
            borderRadius: 6,
            fontSize: 12,
            color: "var(--color-text-primary)",
          }}
          formatter={(value) => {
            const n = typeof value === "number" ? value : 0;
            return metric === "cost_usd"
              ? [formatCost(n), "Cost"]
              : [n.toLocaleString(), metric.replace("_", " ")];
          }}
        />
        <Bar dataKey={metric} radius={[0, 4, 4, 0]}>
          {sorted.map((row, i) => (
            <Cell key={row.group} fill={PALETTE[i % PALETTE.length] ?? "var(--color-accent-primary)"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
