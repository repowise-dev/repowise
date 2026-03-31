"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import type { HotspotResponse } from "@/lib/api/types";

interface ChurnHistogramProps {
  hotspots: HotspotResponse[];
}

const BUCKET_COLORS = [
  "#22c55e", "#22c55e", "#22c55e", "#22c55e", "#22c55e",
  "#eab308", "#eab308", "#f59520",
  "#ef4444", "#ef4444",
];

export function ChurnHistogram({ hotspots }: ChurnHistogramProps) {
  const buckets = Array(10).fill(0) as number[];
  for (const h of hotspots) {
    const idx = Math.min(9, Math.floor(h.churn_percentile / 10));
    buckets[idx]++;
  }

  const data = buckets.map((count, i) => ({
    range: `${i * 10}–${(i + 1) * 10}`,
    count,
  }));

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
        <XAxis
          dataKey="range"
          tick={{ fill: "var(--color-text-tertiary)", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: "var(--color-text-tertiary)", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          allowDecimals={false}
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
          formatter={(value: number) => [`${value} files`, "Count"]}
          labelFormatter={(label: string) => `Churn ${label}%`}
        />
        <Bar dataKey="count" radius={[4, 4, 0, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={BUCKET_COLORS[i]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
